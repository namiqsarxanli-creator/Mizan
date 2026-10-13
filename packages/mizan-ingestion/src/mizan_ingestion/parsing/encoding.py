"""Encoding detection for e-qanun's Word-exported HTML.

The files declare ``charset=windows-1251`` but some versions are really windows-1254. Decoded
with the wrong code page, Azerbaijani letters turn into Cyrillic ("ı" -> "э", "ş" -> "ю",
"İ" -> "Э"), so the text still *looks* like text and nothing fails loudly. We therefore decode
with every candidate and keep the one that produces the fewest Cyrillic letters: genuine
Azerbaijani legal text contains (almost) none.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_META_CHARSET = re.compile(rb"""charset\s*=\s*["']?([\w-]+)""", re.IGNORECASE)
_CYRILLIC = re.compile(r"[\u0400-\u04FF]")
_CANDIDATES = ("windows-1254", "windows-1251")


@dataclass(frozen=True, slots=True)
class DecodedText:
    text: str
    encoding: str
    cyrillic_chars: int


def declared_charset(raw: bytes) -> str | None:
    match = _META_CHARSET.search(raw[:4096])
    return match.group(1).decode("ascii").lower() if match else None


def _is_utf8(raw: bytes) -> bool:
    if raw.isascii():
        return False  # no evidence either way; let the declared charset decide
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def decode_html(raw: bytes) -> DecodedText:
    if _is_utf8(raw):
        text = raw.decode("utf-8")
        return DecodedText(text, "utf-8", len(_CYRILLIC.findall(text)))

    declared = declared_charset(raw)
    candidates = [declared] if declared else []
    candidates += [c for c in _CANDIDATES if c != declared]

    best: DecodedText | None = None
    for encoding in candidates:
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        # Only visible text matters; markup and Word's XML blocks are ASCII anyway.
        score = len(_CYRILLIC.findall(text))
        if best is None or score < best.cyrillic_chars:
            best = DecodedText(text, encoding, score)
        if score == 0:
            break
    if best is None:
        raise ValueError("could not decode document with any candidate encoding")
    return best
