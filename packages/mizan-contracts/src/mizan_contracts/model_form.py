"""Recognise clauses that reproduce the Labour Code's own model contract (annex 1).

The model form is part of the Code, so a clause that repeats its wording cannot contradict the
Code. On the development set, the LLM's only false alarms were such a clause: the form's
termination clause, read against art. 76 (evals/results/f10_contracts). Matching clauses are
not sent to the model. The rules still run on them.

A contract clause matches a template clause when:

1. **Its words are a subsequence of the template's words.** Contracts trim the form, but they
   may not add words: a filled-in salary, a changed number or an added "not" breaks the match.
2. **Nothing that bounds a right was dropped.** Trimming may not drop words like "çox
   olmayaraq" (not more than), "yalnız" (only), "azı" (at least) or "bilməz" (may not).
   "İşçi gündə 8 saatdan … çox olmayaraq işləyir" trimmed to "İşçi gündə 8 saatdan … işləyir"
   is no longer the form.
3. **It is long enough to mean something:** at least eight words.

The template text is the version of annex 1 in force on 2025-12-30, from e-qanun.az
(``data/model_form.txt``).
"""

from __future__ import annotations

import re
from functools import cache
from importlib.resources import files

from mizan_ingestion.text import az_lower

_WORD = re.compile(r"[a-zçəğıöşü0-9]+(?:-[a-zçəğıöşü]+)?")
_TEMPLATE_CLAUSE = re.compile(r"(?:^|\s)(\d{1,2}\.\d{1,2})\.\s")
# Words whose loss changes a limit or a permission.
GUARDS = frozenset(
    {"çox", "az", "azı", "olmayaraq", "yalnız", "bilməz", "olmaz", "ən", "qadağan", "istisna",
     "artıq", "heç", "deyil", "yox", "olmadan", "etmədən", "məcbur"}
)  # fmt: skip
MIN_WORDS = 8


def words(text: str) -> list[str]:
    return _WORD.findall(az_lower(text))


@cache
def templates() -> dict[str, tuple[str, ...]]:
    raw = files("mizan_contracts").joinpath("data/model_form.txt").read_text(encoding="utf-8")
    body = "\n".join(line for line in raw.splitlines() if not line.startswith("#"))
    marks = list(_TEMPLATE_CLAUSE.finditer(body))
    out: dict[str, tuple[str, ...]] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        out[m.group(1)] = tuple(words(body[m.end() : end]))
    return out


def _reproduces(clause: list[str], template: tuple[str, ...]) -> bool:
    used = [False] * len(template)
    j = 0
    for w in clause:
        while j < len(template) and template[j] != w:
            j += 1
        if j == len(template):
            return False  # a word the template does not have here
        used[j] = True
        j += 1
    dropped = {w for w, u in zip(template, used, strict=True) if not u}
    return not dropped & GUARDS


def model_form_clause(text: str) -> str | None:
    """The number of the annex-1 clause this text reproduces, if any."""
    clause = words(text)
    if len(clause) < MIN_WORDS:
        return None
    for number, template in templates().items():
        if _reproduces(clause, template):
            return number
    return None
