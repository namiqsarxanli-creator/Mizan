"""Tokenizer efficiency on Azerbaijani legal text (F8 week 18).

Every token costs context window, latency and, on an API, money. Most open models' tokenizers
were trained on little Azerbaijani, so the same sentence can cost several times more tokens than
in English. The cost differs by model family, and it should inform the base model choice as much
as benchmark scores do.

Measured per tokenizer, on two corpora (law text, and people's questions):

- **fertility:** tokens per whitespace word. 1.0 means every word is a single token.
- **chars per token:** the inverse view, comparable across scripts.
- **fragmented words:** the share of words split into three or more tokens. These are the words
  a model must reassemble from pieces, which tends to hurt small models most.

Words are encoded on their own, and SentencePiece tokenizers (XLM-R, bge-m3) then may prepend a
word-boundary piece that running text would merge. Counts are taken relative to the same
tokenizer's count for the plain letter "a", so that artefact cancels out.

(A per-letter test was tried and dropped: "ğ" and "ı" almost never start an Azerbaijani word, so
a tokenizer can lack a word-initial piece for them and still encode them well inside words.)
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

_WORD = re.compile(r"\w+", re.UNICODE)


def _overhead(tokenizer: Any) -> int:
    """Pieces a lone single-token string costs beyond its one token (SentencePiece's "▁")."""
    return len(tokenizer.encode("a", add_special_tokens=False)) - 1


def corpus_stats(tokenizer: Any, texts: Sequence[str]) -> dict[str, float]:
    words = [w for t in texts for w in _WORD.findall(t)]
    tokens = sum(len(tokenizer.encode(t, add_special_tokens=False)) for t in texts)
    chars = sum(len(t) for t in texts)
    extra = _overhead(tokenizer)
    pieces = [len(tokenizer.encode(w, add_special_tokens=False)) - extra for w in words]
    return {
        "tokens": tokens,
        "fertility": round(tokens / max(len(words), 1), 3),
        "chars_per_token": round(chars / max(tokens, 1), 3),
        "fragmented_words": round(sum(p >= 3 for p in pieces) / max(len(pieces), 1), 3),
    }
