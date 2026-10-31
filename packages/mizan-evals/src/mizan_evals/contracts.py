"""Contract analysis against labelled contracts (F10).

Unit: a clause. Positive: ``violation`` (and ``injection`` for the screen). Reported per
layer:

- **rules**: deterministic; runs offline, so CI checks that the rules still find every planted
  violation with no false positive on the synthetic set (a regression gate, not a claim);
- **llm**: the LLM review alone on *every* clause the screen lets through, including those a
  rule already flags. That measures the layer that has to generalise;
- **combined**: what the product reports (rule findings, plus LLM findings elsewhere).

For each layer: precision, recall, F1, the share of detected violations whose cited article is
one of the labelled ones, false positives on lawful clauses, and how many violations and risks
the LLM produced without a verifiable quote (dropped).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from mizan_contracts.clauses import split_clauses
from mizan_contracts.review import ClauseReviewer
from mizan_contracts.rules import Finding, check, context_of
from mizan_core.logging import get_logger

log = get_logger(__name__)

DATASET = Path("evals/datasets/contracts")
AS_OF = date(2026, 10, 31)

Key = tuple[str, str]  # (contract, clause)


@dataclass(frozen=True, slots=True)
class Label:
    expect: str  # ok | violation | injection
    issue: str | None
    articles: frozenset[str]


def load(dataset: Path = DATASET) -> tuple[dict[str, str], dict[Key, Label]]:
    texts = {p.stem: p.read_text(encoding="utf-8") for p in sorted((dataset / "v1").glob("*.txt"))}
    labels: dict[Key, Label] = {}
    for line in (dataset / "labels.jsonl").read_text(encoding="utf-8").splitlines():
        if line:
            r = json.loads(line)
            labels[(r["contract"], r["clause"])] = Label(
                r["expect"], r["issue"], frozenset(r["articles"])
            )
    return texts, labels


def score(predicted: dict[Key, list[Finding]], labels: dict[Key, Label]) -> dict[str, Any]:
    """Clause-level metrics; ``predicted`` holds the findings per clause (empty: ok)."""
    tp = fp = fn = article_hits = 0
    false_positives: list[dict[str, Any]] = []
    misses: list[dict[str, Any]] = []
    risks_on_ok = 0
    for key, label in labels.items():
        if label.expect == "injection":
            continue
        found = predicted.get(key, [])
        violations = [f for f in found if f.verdict == "violation"]
        if label.expect == "violation":
            if violations:
                tp += 1
                cited = {a for f in violations for a in f.articles}
                article_hits += bool(cited & label.articles)
            else:
                fn += 1
                misses.append({"contract": key[0], "clause": key[1], "issue": label.issue})
        else:
            if violations:
                fp += 1
                false_positives.append({
                    "contract": key[0], "clause": key[1],
                    "articles": sorted({a for f in violations for a in f.articles}),
                    "explanation": violations[0].explanation,
                })  # fmt: skip
            elif any(f.verdict == "risk" for f in found):
                risks_on_ok += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "violations": tp + fn,
        "lawful_clauses": sum(label.expect == "ok" for label in labels.values()),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "right_article": round(article_hits / tp, 3) if tp else None,
        "risk_on_lawful": risks_on_ok,
        "misses": misses,
        "false_positives": false_positives,
    }


def injections(predicted: dict[Key, list[Finding]], labels: dict[Key, Label]) -> dict[str, int]:
    flagged = {k for k, fs in predicted.items() if any(f.verdict == "injection" for f in fs)}
    expected = {k for k, label in labels.items() if label.expect == "injection"}
    return {
        "expected": len(expected),
        "caught": len(flagged & expected),
        "false": len(flagged - expected),
    }


def _group(findings: Iterable[Finding], contract: str) -> dict[Key, list[Finding]]:
    out: dict[Key, list[Finding]] = {}
    for f in findings:
        out.setdefault((contract, f.clause), []).append(f)
    return out


def rule_predictions(texts: dict[str, str]) -> dict[Key, list[Finding]]:
    predicted: dict[Key, list[Finding]] = {}
    for contract, text in texts.items():
        predicted |= _group(check(split_clauses(text)), contract)
    return predicted


def evaluate_rules(dataset: Path = DATASET) -> dict[str, Any]:
    texts, labels = load(dataset)
    predicted = rule_predictions(texts)
    return {
        "contracts": len(texts),
        "clauses": len(labels),
        "rules": score(predicted, labels),
        "injections": injections(predicted, labels),
    }


class PacedClient:
    """A Groq client that waits between calls and retries rate limits (free tier: 8k TPM)."""

    def __init__(self, client: Any, pace_s: float) -> None:
        self._client = client
        self._pace = pace_s
        self.chat = self
        self.completions = self

    async def create(self, **kwargs: Any) -> Any:
        import groq

        for attempt in range(6):
            try:
                response = await self._client.chat.completions.create(**kwargs)
            except groq.RateLimitError as exc:
                retry = exc.response.headers.get("retry-after", "")
                wait = float(retry) if retry.replace(".", "", 1).isdigit() else 30.0 * (attempt + 1)
                log.warning("rate_limited", wait_s=wait)
                await asyncio.sleep(min(wait, 120))
                continue
            await asyncio.sleep(self._pace)
            return response
        raise RuntimeError("rate limited six times in a row")


async def evaluate_llm(
    reviewer: ClauseReviewer, out: Path, dataset: Path = DATASET
) -> dict[str, Any]:
    """Review every contract (resuming from ``out/llm_findings.jsonl``) and score all layers."""
    texts, labels = load(dataset)
    out.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240  once, before any await
    raw = out / "llm_findings.jsonl"
    done: dict[str, dict[str, Any]] = {}
    if raw.exists():
        for line in raw.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            done[row["contract"]] = row
    for contract, text in texts.items():
        if contract in done:
            continue
        clauses = split_clauses(text)
        screened = {f.clause for f in check(clauses) if f.verdict == "injection"}
        pending = [c for c in clauses if c.number not in screened]
        findings, stats = await reviewer.review(pending, as_of=AS_OF, ctx=context_of(clauses))
        row = {
            "contract": contract,
            "findings": [
                {"clause": f.clause, "verdict": f.verdict, "articles": list(f.articles),
                 "explanation": f.explanation, "quote": f.quote}
                for f in findings
            ],
            "stats": {"batches": stats.batches, "failed": stats.failed,
                      "unverified": stats.unverified, "input_tokens": stats.input_tokens,
                      "output_tokens": stats.output_tokens},
        }  # fmt: skip
        done[contract] = row
        with raw.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        log.info("contract_reviewed", contract=contract, **row["stats"])

    llm: dict[Key, list[Finding]] = {}
    for contract, row in done.items():
        for f in row["findings"]:
            llm.setdefault((contract, f["clause"]), []).append(
                Finding(f["clause"], f["verdict"], "llm", tuple(f["articles"]),
                        f["explanation"], "llm", f["quote"])
            )  # fmt: skip
    rules = rule_predictions(texts)
    combined = dict(rules)
    for key, found in llm.items():
        combined.setdefault(key, found)  # a rule's verdict stands; the LLM fills the rest
    totals = {
        k: sum(row["stats"][k] for row in done.values())
        for k in ("batches", "failed", "unverified", "input_tokens", "output_tokens")
    }
    return {
        "contracts": len(texts),
        "clauses": len(labels),
        "as_of": AS_OF.isoformat(),
        "rules": score(rules, labels),
        "llm": score(llm, labels),
        "combined": score(combined, labels),
        "injections": injections(rules, labels),
        "llm_calls": totals,
    }
