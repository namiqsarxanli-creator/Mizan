"""Package AzLegalBench for the Hugging Face Hub (F11). Builds locally; uploads nothing.

Files sit at the top level (no folders), so the package can also be uploaded through the Hub's
web form, which drops folder paths.

    python infra/hf/build_azlegalbench.py            ->  dist/azlegalbench/

The package holds the evaluation sets of this repository, unchanged, as JSON Lines, with a
dataset card (``README.md``) that states how each set was made and what it can and cannot
show. Publishing is a separate, deliberate step, run by the owner:

    huggingface-cli upload <namespace>/azlegalbench dist/azlegalbench --repo-type dataset
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "evals/datasets"
OUT = ROOT / "dist/azlegalbench"

# (config name, source file, description)
JSONL = [
    ("qa", "v1/questions.jsonl", "200 questions with gold articles, by type"),
    ("paraphrases", "v1/paraphrases.jsonl", "185 questions in three phrasings each"),
    ("multihop", "v1/multihop.jsonl", "40 questions needing two linked articles"),
    ("multihop_b", "v1/multihop_b.jsonl", "30 more, built to test a selected policy"),
    ("court_decisions", "v1/court_decisions.jsonl", "11 questions governed by Constitutional Court decisions"),
    ("retrieval_v0", "v0/retrieval.jsonl", "70 retrieval questions (the CI gate's set)"),
    ("redteam", "redteam/v1.jsonl", "143 prompt-injection attacks and hard benign items, dev/test split"),
]  # fmt: skip
CONTRACT_SETS = [
    ("contracts_dev", "contracts", "v1", "12 contracts, same author as the checker"),
    ("contracts_test1", "contracts/test", "contracts", "10 contracts, independent author"),
    ("contracts_test2", "contracts/test2", "contracts", "10 contracts, independent author"),
    ("contracts_test3", "contracts/test3", "contracts", "10 contracts, independent author"),
]


def _rows(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def build() -> dict[str, int]:
    if OUT.exists():
        shutil.rmtree(OUT)
    counts: dict[str, int] = {}
    for name, source, _ in JSONL:
        rows = _rows(DATA / source)
        _write(OUT / f"{name}.jsonl", rows)
        counts[name] = len(rows)
    for name, folder, texts, _ in CONTRACT_SETS:
        base = DATA / folder
        labels = _rows(base / "labels.jsonl")
        by_contract: dict[str, list[dict[str, object]]] = {}
        for label in labels:
            by_contract.setdefault(str(label["contract"]), []).append(
                {k: v for k, v in label.items() if k != "contract"}
            )
        rows = [
            {
                "contract": p.stem,
                "text": p.read_text(encoding="utf-8"),
                "clauses": by_contract[p.stem],
            }
            for p in sorted((base / texts).glob("*.txt"))
        ]
        _write(OUT / f"{name}.jsonl", rows)
        counts[name] = len(rows)
    (OUT / "README.md").write_text(card(counts), encoding="utf-8")
    return counts


def card(counts: dict[str, int]) -> str:
    configs = "\n".join(
        f"  - config_name: {name}\n    data_files: {name}.jsonl"
        for name in [*(n for n, *_ in JSONL), *(n for n, *_ in CONTRACT_SETS)]
    )
    table = (
        "\n".join(f"| `{name}` | {counts[name]} | {desc} |" for name, _, desc in JSONL)
        + "\n"
        + "\n".join(
            f"| `{name}` | {counts[name]} contracts | {desc} |"
            for name, _, _, desc in CONTRACT_SETS
        )
    )
    return f"""---
language:
  - az
license: cc-by-4.0
pretty_name: AzLegalBench
task_categories:
  - question-answering
  - text-retrieval
  - text-classification
tags:
  - legal
  - azerbaijani
  - rag
  - labour-law
size_categories:
  - n<1K
configs:
{configs}
---

# AzLegalBench

Evaluation sets for legal question answering and contract review over the **Labour Code of the
Republic of Azerbaijan** (Əmək Məcəlləsi, e-qanun.az id 46943), from the
[Mizan](https://github.com/namiqsarxanli-creator/Mizan) project. The questions are in
colloquial Azerbaijani, and the gold labels are article keys (`art:69`).

| config | size | what it is |
|---|---|---|
{table}

## How it was made, and its limits (read before using)
**Questions are silver.** An LLM (Claude) wrote them from the article texts. Every label was
then checked mechanically against the Code as in force on the question's date, and false
premises were checked against the article text. No lawyer has reviewed them. Treat recall
against `relevant` as a lower bound: it lists the articles a lawyer would cite first, not
every useful one.

**Contracts are synthetic.** No real contract or personal data is included; every name, tax
number and address is invented.

- `contracts_dev` was written by the same author as the checker it was used to develop.
- The three test sets were written and labelled by another model (OpenAI Codex), which had
  not seen the checker. Labels were checked against the Code before use; the corrections are
  listed per set (`LABEL_CHANGES.md` in the repository).

**Temporal questions** carry an `as_of` date and must be answered from the text in force on
that date.

**The red-team set** is split into `dev` and `test` by a hash of the id. Tune on `dev`
only.

## Baselines (Mizan, 2026)
| task | system | result |
|---|---|---|
| retrieval, `qa` | bge-m3 dense, article chunks | Recall@5 0.946, MRR@10 0.894 |
| retrieval, `multihop` / `multihop_b` | + citation-graph slot | Recall@5 0.912 / 0.817 (dense 0.838 / 0.700) |
| answering, 58 of `qa` | gpt-oss-120b, single-shot RAG, verbatim quote check | correct 0.914 (LLM judge, uncalibrated) |
| `court_decisions` | with / without decisions in context | correct 0.91 / 0.27 |
| `redteam` test | pattern screen | 98.2% of attacks blocked, 0 benign |
| `contracts_test1..3` | LLM review + rules | precision 1.00 / 0.67 / 0.59, recall 0.28 / 0.50 / 0.61 |

Methods, confidence intervals and the negative results are in the
[technical report](https://github.com/namiqsarxanli-creator/Mizan/blob/main/docs/REPORT.md).

## License
- **Questions, labels and contracts:** CC BY 4.0.
- **Law texts quoted in the data:** official legal texts of the Republic of Azerbaijan
  (e-qanun.az).
- **Not legal advice.**
"""


if __name__ == "__main__":
    print(json.dumps(build(), indent=1))
