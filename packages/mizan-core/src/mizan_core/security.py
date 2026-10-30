"""Prompt-injection screening for text that reaches a model from outside (F10).

``screen(text, channel)`` is deterministic and costs no LLM call, so it runs on every request
and in CI (``mizan-eval redteam``). It is the first of several layers:

1. **Screen** user questions and uploaded documents (this module). Clear attacks are refused
   before they cost anything.
2. **Delimit** everything that is not ours. Sources and documents go inside tags, and every
   system prompt says that text inside them is data, never instructions. ``neutralise``
   removes forged tags and invisible characters from untrusted text first.
3. **Constrain** what the model can do. Answers are JSON against a schema. Every claim must
   quote a provided source, and the quote is checked mechanically. The agent's tools are a
   fixed, read-only allowlist; the MCP tools are annotated read-only.
4. **Check** the output: ``leaks_prompt`` refuses an answer that reproduces a system prompt.

**How the screen works.** It matches rules against several views of the text, so that
disguises are undone before matching:

- Unicode NFKC normalisation; zero-width and bidirectional controls removed;
- Cyrillic look-alikes inside Latin words mapped back;
- leetspeak digits read as letters;
- letter-spaced text joined;
- base64 payloads decoded.

The rules target instructions *to the model* (override, prompt extraction, persona, forged
prompt structure, exfiltration, tool abuse, notes to an AI inside a contract). They do not
target the words alone: "təlimat" (instruction), "göstəriş" (order) and "sistem" (system) are
everyday labour-law vocabulary, and the red-team set's hard benign items keep that honest.

A screen is bypassable by a motivated attacker. Its job is to stop the cheap attacks and to
make the expensive ones visible; layers 2–4 do not depend on it.
"""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

Channel = Literal["question", "document"]

_INVISIBLE = re.compile("[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
_CYRILLIC_TO_LATIN = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i", "ј": "j",
    "ѕ": "s", "ԁ": "d", "ɡ": "g", "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C", "Х": "X",
    "І": "I", "Т": "T", "Н": "H", "К": "K", "М": "M", "В": "B",
})  # fmt: skip
_LEET = str.maketrans(
    {"4": "a", "3": "e", "1": "i", "0": "o", "5": "s", "7": "t", "@": "a", "$": "s"}
)
_LATIN = re.compile(r"[A-Za-z]")
_CYRILLIC = re.compile(r"[\u0400-\u04ff]")
_BASE64 = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")


def _unconfuse(word: str) -> str:
    """Map Cyrillic look-alikes to Latin, only in words that mix the two scripts."""
    if _LATIN.search(word) and _CYRILLIC.search(word):
        return word.translate(_CYRILLIC_TO_LATIN)
    return word


def _deleet(word: str) -> str:
    """Read digits as letters, only in words that mix the two (``1gn0r3``, not ``2024``)."""
    if any(c.isalpha() for c in word) and any(c in "431057@$" for c in word):
        return word.translate(_LEET)
    return word


def _unspace(text: str) -> str:
    """``i g n o r e   a l l`` -> ``ignore all``: letters one space apart, words further apart."""
    tokens = text.split()
    if len(tokens) < 8 or sum(len(t) == 1 for t in tokens) < 0.7 * len(tokens):
        return text
    return re.sub(r" {2,}", "\x00", text).replace(" ", "").replace("\x00", " ")


def _decoded_base64(text: str) -> list[str]:
    out = []
    for token in _BASE64.findall(text):
        try:
            decoded = base64.b64decode(token + "=" * (-len(token) % 4), validate=True)
            plain = decoded.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            continue
        if sum(c.isprintable() for c in plain) >= 0.9 * len(plain):
            out.append(plain)
    return out


def views(text: str) -> list[str]:
    """The text as it may be meant: undisguised, lower-cased, deduplicated."""
    base = _INVISIBLE.sub("", unicodedata.normalize("NFKC", text))
    base = " ".join(_unconfuse(w) for w in base.split(" "))
    joined = _unspace(base)
    candidates = [base, joined, " ".join(_deleet(w) for w in base.split(" "))]
    for payload in _decoded_base64(base):
        candidates.extend(views(payload))
    seen: dict[str, None] = {}
    for c in candidates:
        # "İ".lower() is "i" + a combining dot, which breaks \b and every rule after it.
        seen.setdefault(c.replace("İ", "i").lower(), None)
    return list(seen)


def _rx(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE | re.DOTALL)


# Qualifiers that make "the instructions" mean the model's own, not an employer's.
_THEIRS_EN = r"(?:all\W+)?(?:of\W+)?(?:the\W+|your\W+|any\W+|my\W+)?(?:previous|prior|above|earlier|preceding|system|initial|original|old|current)"
_THEIRS_AZ = r"(?:əvvəlki|yuxarıdakı|sistem|sənə\s+verilmiş|ilkin)"

RULES: dict[str, re.Pattern[str]] = {
    "override": _rx(
        rf"\b(?:ignore|disregard|forget|override|bypass|skip)\W+{_THEIRS_EN}\W+(?:instructions?|rules?|prompts?|directions?|guidelines?|messages?)",
        r"\b(?:ignore|disregard|forget|drop)\W+(?:all\W+)?(?:your|the)\W+(?:rules|instructions|guidelines)",
        r"\bforget\W+everything\W+(?:you\W+were|you've\W+been|above)",
        r"\bnew\W+instructions?\W+(?:override|replace|supersede)",
        r"\bfollow\W+only\W+my\W+instructions",
        r"\bstop\W+being\W+(?:a|an|the)\W+\w+\W+assistant",
        r"\byour\W+new\W+(?:task|role|instructions?)\W+(?:is|are)",
        rf"{_THEIRS_AZ}\W+(?:bütün\W+)?(?:təlimat|qayda|göstəriş)\w*\W+(?:unut|məhəl\W+qoyma|nəzərə\W+alma|ləğv\W+et|etibarsız)",
        r"\b(?:bütün|əvvəlki)\W+(?:təlimat|göstəriş)\w*\W+unut",
        r"\byuxarıdakı\W+qaydalar\w*\W+məhəl",
        r"\bbundan\W+sonra\W+(?:yalnız|ancaq)\W+mənim",
        r"\b(?:əvvəlki|köhnə)\W+(?:göstəriş|təlimat)\w*\W+etibarsız",
        r"\bqaydaları\W+pozmağın\W+vaxtı",
        r"(?:игнорируй|забудь|не\W+обращай\W+внимания\W+на|отмени)\W+(?:все\W+)?(?:предыдущие|свои|системные|прежние|твои)?\W*(?:инструкции|правила|указания)",
        r"(?:önceki|tüm)\W+(?:talimat|kural)\w*\W+(?:yok\W+say|unut|görmezden)",
        r"\bkurallarını\W+unut",
    ),
    "no_sources": _rx(
        r"\b(?:mənbəsiz|istinadsız|yoxlamasız)\b",
        r"\bmənbə\W+göstərmə(?:dən|k\W+məcburiyyətin)",
        r"\bwithout\W+(?:citing\W+)?(?:any\W+)?(?:sources?|citations?)\b",
        r"\bбез\W+(?:ссылок|источников)",
        r"\bkaynak\W+göstermeden",
    ),
    "prompt_extraction": _rx(
        r"\bsystem\W+prompt",
        r"\b(?:hidden|secret|initial|original)\W+(?:instructions?|prompt|rules)",
        r"\brepeat\W+(?:the\W+)?(?:text|words|everything)\W+above",
        r"\bsistem\W+prompt",
        r"\b(?:gizli|ilkin)\W+təlimat",
        r"\bsənə\W+verilmiş\W+(?:gizli\W+)?təlimat",
        r"\bilk\W+mesaj\w*\W+(?:bütün\W+)?mətn",
        r"\bhansı\W+qaydalarla\W+işlədiyini",
        r"системн\w*\W+промпт",
        r"скрыт\w*\W+инструкц",
        r"\bsistem\W+istem",
    ),
    "persona": _rx(
        r"(?-i:\bDAN\b)",
        r"\bdeveloper\W+(?:mode|rejim)",
        r"\b(?:no|without)\W+(?:any\W+)?(?:restrictions|filters|limits|rules)\b",
        r"\bunfiltered\b|\bjailbreak",
        r"\bpretend\W+(?:you\W+are|to\W+be)\b",
        r"\byou\W+are\W+now\W+(?:a|an|my)?\W*(?:\w+\W+){0,3}?(?:ai|model|assistant|bot|dan)\b",
        r"\bno\W+matter\W+what\W+the\W+law",
        r"\bheç\W+bir\W+(?:qaydası|məhdudiyyəti)\W+olmayan",
        r"\bməhdudiyyətsiz\b",
        r"\bbütün\W+məhdudiyyətləri\W+söndür",
        r"\brolunu\W+dəyiş",
        r"\bбез\W+ограничений|режим\W+разработчика|отключи\W+(?:все\W+)?фильтры",
    ),
    "forged_structure": _rx(
        r"</?\s*(?:source|system|assistant|user|instructions?)\b[^>]*>",
        r"<\|\s*im_(?:start|end)\s*\|>|\[/?inst\]|<</?sys>>",
        r"(?:^|\n)\s*#{2,}\s*(?:system|user|assistant|система|пользователь|sistem)\s*:",
        r"\"role\"\s*:\s*\"(?:system|assistant)\"",
        r"<!--.*?-->",
    ),
    "exfiltration": _rx(
        r"!\[[^\]]*\]\(\s*https?://",
        r"(?:include|add|append|insert|əlavə\W+et|добавь)\W+(?:\w+\W+){0,6}?(?:link|linki|ссылку|url|https?://)",
        r"(?:link|linki)\w*\W+(?:\w+\W+){0,4}?(?:şifrə|password|fin\b|пароль)",
        r"\b(?:other|digər)\W+(?:users?|istifadəçilər)\w*\W+(?:\w+\W+){0,3}?(?:questions?|sual|asked|verdiyi)",
        r"других\W+пользователей",
    ),
    "tool_abuse": _rx(
        r"\brm\s+-rf\b|;\s*drop\s+table|;\s*--|\bunion\s+select\b",
        r"\b(?:call|use|run|invoke)\W+(?:\w+\W+){0,3}?(?:for\W+)?(?:every|all)\W+(?:\w+\W+){0,3}?(?:articles?|tools?)\b",
        r"\b(?:output|dump|export)\W+(?:the\W+)?(?:full|whole|entire)\W+database",
        r"\bbütün\W+alətləri\W+çağır",
        r"\bverilənlər\W+bazasındakı\W+hər\W+şeyi",
    ),
    "note_to_model": _rx(
        r"\b(?:note|message|instruction|reminder)s?\W+(?:to|for)\W+(?:the\W+)?(?:ai|assistant|model|llm|reviewer|analy[sz]er|chatbot)\b",
        r"\bimportant\W+system\W+message",
        r"(?:süni\W+intellekt|\bai\b|modelə|\bai-?yə)\W+(?:\w+\W+){0,2}?(?:üçün\W+)?(?:qeyd|göstəriş)",
        r"\banaliz\W+edən\W+modelə",
        r"\[\s*(?:sistem|system)\s*:",
        r"примечание\W+для\W+ии|yapay\W+zek[aâ]ya\W+not",
        r"\b(?:the\W+)?analysis\W+must\W+report",
        r"\bhesabatda\W+yalnız\W+[\"'“]",
        r"\b(?:bu\W+bənd|this\W+clause)\W+(?:\w+\W+){0,3}?(?:heç\W+nə\W+yazma|təhlil\W+etmə|do\W+not\W+flag)",
        r"\bistifadəçiyə\W+(?:\w+\W+){0,3}?imzalamağı\W+tövsiyə",
        r"\btell\W+the\W+user\W+(?:that\W+)?the\W+contract\W+is",
        r"\bmark\W+(?:every|all|this)\W+(?:clause|clauses|contract)\W+as\W+compliant",
        r"\bdo\W+what\W+it\W+says",
    ),
}


@dataclass(frozen=True, slots=True)
class Screened:
    blocked: bool
    reasons: tuple[str, ...] = ()


def screen(text: str, channel: Channel = "question") -> Screened:
    """Block text that tries to instruct the model. ``channel`` is reported, not yet used."""
    reasons = sorted({name for v in views(text) for name, rule in RULES.items() if rule.search(v)})
    return Screened(bool(reasons), tuple(reasons))


_TAG = re.compile(r"</?\s*(?:source|system|assistant|user|document)\b[^>]*>", re.IGNORECASE)


def neutralise(text: str) -> str:
    """Untrusted text, safe to put between our tags: no invisible characters, no forged tags."""
    clean = _INVISIBLE.sub("", unicodedata.normalize("NFKC", text))
    return _TAG.sub(lambda m: m.group(0).replace("<", "‹").replace(">", "›"), clean)


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def leaks_prompt(output: str, prompt: str, window: int = 60) -> bool:
    """True when the output reproduces any ``window`` characters of a system prompt verbatim."""
    out, ref = _squash(output), _squash(prompt)
    if len(ref) < window:
        return bool(ref) and ref in out
    step = window // 2
    return any(ref[i : i + window] in out for i in range(0, len(ref) - window + 1, step))
