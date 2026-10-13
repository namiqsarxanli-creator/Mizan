"""Azerbaijani text utilities."""

from __future__ import annotations

import re
import unicodedata

_WS = re.compile(r"\s+")
_INVISIBLE = dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\ufeff"), None)


_LOWER = "a-zçəğıöşü"
# Word "small caps" exports leave capital İ/I inside lowercase words: "Respublİkasının".
_SMALL_CAPS_I_DOTTED = re.compile(rf"(?<=[{_LOWER}])İ(?=[{_LOWER}]|\b)")
_SMALL_CAPS_I_DOTLESS = re.compile(rf"(?<=[{_LOWER}])I(?=[{_LOWER}]|\b)")


def normalize(text: str) -> str:
    """Canonical form used for storage, hashing and comparison.

    NFC (so "ə" is one code point however it was typed), no invisible characters, all
    whitespace (including NBSP from Word) collapsed to single spaces, and small-caps casing
    artefacts repaired.
    """
    text = unicodedata.normalize("NFC", text).translate(_INVISIBLE)
    text = _SMALL_CAPS_I_DOTTED.sub("i", text)
    text = _SMALL_CAPS_I_DOTLESS.sub("ı", text)
    return _WS.sub(" ", text).strip()


def az_lower(text: str) -> str:
    """Lowercase with Azerbaijani dotted/dotless i rules ("İ" -> "i", "I" -> "ı")."""
    return text.replace("İ", "i").replace("I", "ı").lower()


_CARDINALS: dict[str, int] = {
    "bir": 1, "iki": 2, "üç": 3, "dörd": 4, "beş": 5,
    "altı": 6, "yeddi": 7, "səkkiz": 8, "doqquz": 9,
    "on": 10, "iyirmi": 20, "otuz": 30, "qırx": 40, "əlli": 50,
    "altmış": 60, "yetmiş": 70, "səksən": 80, "doxsan": 90, "yüz": 100,
}  # fmt: skip
_ORDINAL_SUFFIX = re.compile(r"^[ıiuü]?nc[ıiuü]$")


def _ordinal_word(word: str) -> int | None:
    # Try the longest matching stem: "altıncı" must resolve to "altı", not "al...".
    for stem in sorted(_CARDINALS, key=len, reverse=True):
        if word.startswith(stem) and _ORDINAL_SUFFIX.match(word[len(stem) :]):
            return _CARDINALS[stem]
    return None


def parse_ordinal(phrase: str) -> int | None:
    """Parse an Azerbaijani ordinal numeral: "Birinci" -> 1, "İyirmi beşinci" -> 25."""
    words = az_lower(normalize(phrase)).split()
    if not words:
        return None
    last = _ordinal_word(words[-1])
    if last is None:
        return None
    total = last
    for word in words[:-1]:
        if word not in _CARDINALS:
            return None
        total += _CARDINALS[word]
    return total


_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
_ROMAN_RE = re.compile(r"^(?=[IVXLC])C{0,3}(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")


def parse_roman(numeral: str) -> int | None:
    numeral = numeral.upper()
    if not _ROMAN_RE.match(numeral):
        return None
    total = 0
    for current, nxt in zip(numeral, [*numeral[1:], ""], strict=True):
        value = _ROMAN[current]
        total += -value if nxt and _ROMAN[nxt] > value else value
    return total


# Suffix of "N-ci" follows vowel harmony with the *spoken* last word of the number:
# 1 bir -> ci, 3 üç -> cü, 6 altı -> cı, 9 doqquz -> cu, 10 on -> cu, 40 qırx -> cı.
_UNIT_SUFFIX = {1: "ci", 2: "ci", 3: "cü", 4: "cü", 5: "ci", 6: "cı", 7: "ci", 8: "ci", 9: "cu"}
_TENS_SUFFIX = {1: "cu", 2: "ci", 3: "cu", 4: "cı", 5: "ci", 6: "cı", 7: "ci", 8: "ci", 9: "cı"}


def ordinal_suffix(n: int) -> str:
    """ "11" -> "11-ci", "3" -> "3-cü", "40" -> "40-cı", "100" -> "100-cü"."""
    if n % 10:
        suffix = _UNIT_SUFFIX[n % 10]
    elif n % 100:
        suffix = _TENS_SUFFIX[(n // 10) % 10]
    else:
        suffix = "cü"  # yüz / min (both end in a front-rounded or front vowel: yüz -> cü)
    return f"{n}-{suffix}"
