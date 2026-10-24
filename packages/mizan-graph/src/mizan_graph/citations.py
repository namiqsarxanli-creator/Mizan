"""Extract article citations from Azerbaijani legal text.

Citations follow a small grammar, which a deterministic extractor handles exactly and
cheaply; it is evaluated against hand-labelled sentences (``tests/test_citations.py``)::

    bu Məcəllənin 70-ci maddəsinin “a” bəndi          -> this act, art. 70
    Azərbaycan Respublikası Əmək Məcəlləsinin 7-ci maddəsinin 2-1-ci hissəsi
                                                      -> the act named "Əmək Məcəlləsi"
    73, 74 və 75-ci maddələrində                      -> three articles
    115–117 və 118-1-ci maddələrində                  -> a range (en dash) and art. 118-1
    Konstitusiyasının 25-ci maddəsinin I hissəsinə və 149-cu maddəsinin ...
                                                      -> both Constitution: an unqualified
                                                         citation continues the sentence's act
    bu maddənin ikinci hissəsi                        -> no number: intra-article, ignored
    (... toplusu, 1999, № 4, maddə 213)               -> gazette item, not an article

A hyphen inside a number is part of it (``118-1`` is an inserted article); only an en or em
dash between numbers is a range.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from mizan_ingestion.text import az_lower

SELF = "self"

_SUFFIX = r"-?\s*c[iıuü]"
_NUMBER = r"\d+(?:[-.]\d+)*"
_ITEM = rf"{_NUMBER}(?:{_SUFFIX})?"
_SEP = r"\s*(?:,|–|—|\bvə\s+ya\b|\bvə\b|\byaxud\b|\bhabelə\b)\s*"
# A list of article numbers whose last item carries the ordinal suffix, then "maddə...".
_CITATION = re.compile(
    rf"(?P<list>(?:{_ITEM}{_SEP})*{_NUMBER}){_SUFFIX}\s+(?P<word>maddə\w*)",
    re.IGNORECASE,
)
_LIST_ITEM = re.compile(rf"(?P<num>{_NUMBER})(?:{_SUFFIX})?(?P<sep>{_SEP})?", re.IGNORECASE)

# Act qualifiers, matched on lower-cased text immediately before the citation.
_THIS_ACT = re.compile(r"\bbu\s+(?:məcəllə(?:nin)?|qanun(?:un)?)\s*$")
_SAME_ACT = re.compile(r"\bhəmin\s+(?:məcəllə(?:nin)?|qanun(?:un)?)\s*$")
# Codes of the Republic, longest names first so "mülki prosessual" wins over "mülki".
CODES = tuple(
    sorted(
        (
            "ailə", "büdcə", "cinayət", "cinayət-prosessual", "cəzaların icrası", "əmək",
            "gömrük", "inzibati prosessual", "inzibati xətalar", "meşə", "mənzil", "miqrasiya",
            "mülki", "mülki prosessual", "seçki", "su", "şəhərsalma və tikinti",
            "ticarət gəmiçiliyi", "torpaq", "vergi", "hava", "təhsil",
        ),
        key=len,
        reverse=True,
    )
)  # fmt: skip
_NAMED_CODE = re.compile(
    r"\b(?P<name>" + "|".join(re.escape(c) for c in CODES) + r")\s+məcəlləsi(?:nin)?\s*$"
)
_ANY_CODE = re.compile(r"\bməcəlləsi(?:nin)?\s*$")
_CONSTITUTION = re.compile(r"\bkonstitusiya(?:sı(?:nın)?|nın)\s*$")
_NAMED_LAW = re.compile(
    r"[“\"«]([^”\"»]{3,160})[”\"»]\s+(?:azərbaycan\s+respublikası(?:nın)?\s+)?qanunu(?:nun)?\s*$"
)
_QUALIFIER_WINDOW = 200
# An act named earlier in the sentence, in the genitive, when nothing qualifies the citation
# itself: "Konstitusiyasının əmək müqaviləsinin ... təsbit edən 35-ci maddəsi". The court is
# not the act: "Konstitusiya Məhkəməsinin" does not count.
_MENTION = re.compile(
    r"(?P<constitution>konstitusiyas[ıi]n[ıi]n|konstitusiyan[ıi]n)(?!\s+(?:məhkəmə|qanun))"
    r"|(?P<this>(?<![a-zəğıöşüç])bu\s+məcəllənin)"
    r"|(?<![a-zəğıöşüç])(?P<code>" + "|".join(re.escape(c) for c in CODES) + r")\s+məcəlləsinin"
)
# Right after a citation, within its clause: "... hallar istisna olmaqla", "... şərtlərdən başqa".
_EXCEPTION = re.compile(
    r"[^.,;:]{0,80}?(?:istisna\s+ol(?:un)?maqla|istisna\s+edilməklə"
    r"|hallardan\s+başqa|hallar\s+istisna|şərtlərdən\s+başqa)"
)
# Words that may sit between an act's name and the citation: "Məcəlləsinin yalnız 70-ci".
_FILLER = re.compile(r"(?:\s+(?:yalnız|isə|da|də|məhz))*\s*$")
_SENTENCE_END = re.compile(r"[.;:!?]\s+(?=[A-ZƏÜÖĞIİŞÇ“\"«(]|\d+\.)|\n")


@dataclass(frozen=True, slots=True)
class Citation:
    act: str  # SELF, or a normalised act name such as "mülki məcəllə" / "konstitusiya"
    number: str  # as cited: "70", "118-1", or a dotted point such as "10-1.2"
    start: int
    end: int
    # "52-ci maddəsində göstərilən hallar istisna olmaqla": the cited provision is an exception
    # to the citing one, i.e. the special norm (lex specialis, NHA Konstitusiya Qanunu 10.4).
    exception: bool = False

    @property
    def article(self) -> str:
        """The cited article: a dotted number cites a point, "10-1.2" lies in article 10-1."""
        return self.number.split(".", 1)[0]

    @property
    def internal(self) -> bool:
        return self.act == SELF


def _expand(listing: str) -> list[str]:
    numbers: list[str] = []
    range_from: str | None = None
    for m in _LIST_ITEM.finditer(listing):
        num = m.group("num")
        if range_from is not None:
            numbers.extend(_range(range_from, num))
            range_from = None
        else:
            numbers.append(num)
        sep = (m.group("sep") or "").strip()
        if sep in {"–", "—"}:
            range_from = numbers.pop()
    if range_from is not None:
        numbers.append(range_from)
    return numbers


def _range(first: str, last: str) -> list[str]:
    if first.isdigit() and last.isdigit() and 0 < int(last) - int(first) <= 50:
        return [str(n) for n in range(int(first), int(last) + 1)]
    return [first, last]  # inserted or dotted numbers: the endpoints are all we know


def _qualifier(before: str, self_names: frozenset[str]) -> str | None:
    """The act named right before a citation; None when it names none."""
    tail = _FILLER.sub("", az_lower(before[-_QUALIFIER_WINDOW:]))
    if _THIS_ACT.search(tail):
        return SELF
    if m := _NAMED_LAW.search(tail):
        name = re.sub(r"\s+", " ", m.group(1)).strip()
        return SELF if name in self_names else f"qanun: {name}"
    if _CONSTITUTION.search(tail):
        return "konstitusiya"
    if m := _NAMED_CODE.search(tail):
        name = f"{m.group('name')} məcəllə"
        return SELF if name in self_names else name
    if _ANY_CODE.search(tail):
        return "məcəllə"  # a code we do not know by name
    if _SAME_ACT.search(tail):
        return ""  # "həmin Məcəllənin": the act cited last in this sentence
    return None


def _last_mention(prefix: str, self_names: frozenset[str]) -> str | None:
    found = None
    for m in _MENTION.finditer(az_lower(prefix)):
        found = m
    if found is None:
        return None
    if found.group("constitution"):
        return "konstitusiya"
    if found.group("this"):
        return SELF
    name = f"{found.group('code')} məcəllə"
    return SELF if name in self_names else name


def extract(
    text: str, *, self_names: frozenset[str] = frozenset({"əmək məcəllə"})
) -> list[Citation]:
    """Article citations in ``text``, with the cited act resolved.

    ``self_names`` are the normalised names by which the document refers to itself.
    """
    citations: list[Citation] = []
    sentence_start = 0
    sentence_act: str | None = None
    breaks = [m.end() for m in _SENTENCE_END.finditer(text)]
    for m in _CITATION.finditer(text):
        while breaks and breaks[0] <= m.start():
            sentence_start = breaks.pop(0)
            sentence_act = None
        # No qualifier, or "həmin Məcəllə": the act cited last in this sentence, else this act.
        prefix = text[sentence_start : m.start()]
        act = (
            _qualifier(prefix, self_names)
            or sentence_act
            or _last_mention(prefix, self_names)
            or SELF
        )
        sentence_act = act
        exception = bool(_EXCEPTION.match(az_lower(text[m.end() : m.end() + 120])))
        citations.extend(
            Citation(act=act, number=number, start=m.start(), end=m.end(), exception=exception)
            for number in _expand(m.group("list"))
        )
    return citations
