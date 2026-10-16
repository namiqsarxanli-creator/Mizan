"""Human labels and judge calibration.

Labelling is *blind*: the labeller sees the question, the gold provisions and the answer, never
the judge's verdict, so the two are independent measurements. Agreement is reported as Cohen's
kappa, which discounts the agreement two raters would reach by chance (with 80% of answers
correct, two raters who both say "correct" at random already agree 68% of the time).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from mizan_evals.run import Record


@dataclass(frozen=True, slots=True)
class Label:
    id: str
    correct: bool
    labeller: str
    at: str


def load_labels(path: Path) -> dict[str, Label]:
    if not path.exists():
        return {}
    labels = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            labels[row["id"]] = Label(**row)  # later lines override earlier ones
    return labels


def append_label(path: Path, label: Label) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(label)) + "\n")


@dataclass(frozen=True, slots=True)
class Agreement:
    n: int
    observed: float
    expected: float
    kappa: float
    # confusion[judge][human], keys "correct" / "incorrect"
    confusion: dict[str, dict[str, int]]

    @property
    def interpretation(self) -> str:
        # Landis & Koch (1977)
        k = self.kappa
        if k < 0.2:
            return "slight"
        if k < 0.4:
            return "fair"
        if k < 0.6:
            return "moderate"
        if k < 0.8:
            return "substantial"
        return "almost perfect"


def cohens_kappa(pairs: Sequence[tuple[bool, bool]]) -> Agreement:
    """Agreement between two binary raters given (judge, human) pairs."""
    n = len(pairs)
    if n == 0:
        raise ValueError("no overlapping labels")
    observed = sum(j == h for j, h in pairs) / n
    judge_yes = sum(j for j, _ in pairs) / n
    human_yes = sum(h for _, h in pairs) / n
    expected = judge_yes * human_yes + (1 - judge_yes) * (1 - human_yes)
    kappa = 1.0 if expected == 1.0 else (observed - expected) / (1 - expected)

    def name(v: bool) -> str:
        return "correct" if v else "incorrect"

    confusion = {
        "correct": {"correct": 0, "incorrect": 0},
        "incorrect": {"correct": 0, "incorrect": 0},
    }
    for j, h in pairs:
        confusion[name(j)][name(h)] += 1
    return Agreement(n, observed, expected, kappa, confusion)


def agreement(records: dict[str, Record], labels: dict[str, Label]) -> Agreement:
    pairs = [
        (bool(records[i].correct), labels[i].correct)
        for i in sorted(labels.keys() & records.keys())
        if records[i].correct is not None
    ]
    return cohens_kappa(pairs)


def render_item(record: Record, gold_text: str) -> str:
    """What the labeller sees. Deliberately excludes the judge's verdict and reason."""
    cites = "\n".join(f"  - [{c['key']}] {c['quote']}" for c in record.citations) or "  (none)"
    return (
        f"\n{'=' * 78}\n{record.id}  type={record.type}  as_of={record.as_of}\n"
        f"\nQUESTION:\n  {record.question}\n"
        f"\nGOLD PROVISIONS ({', '.join(record.gold) or 'none: out of scope'}):\n{gold_text}\n"
        f"\nANSWER:\n  {record.answer}\n\nCITATIONS:\n{cites}\n"
    )


def label_session(
    records: dict[str, Record],
    path: Path,
    *,
    labeller: str,
    gold_text: Callable[[Record], str],
    ask: Callable[[str], str] = input,
    show: Callable[[str], None] = print,
    limit: int | None = None,
) -> int:
    """Interactive loop; returns how many labels were added. Resumable at any point."""
    done = load_labels(path)
    todo = [
        r
        for r in sorted(records.values(), key=lambda r: r.id)
        if r.id not in done and r.error is None
    ]
    added = 0
    for record in todo[:limit]:
        show(render_item(record, gold_text(record)))
        while True:
            reply = ask(
                "Is the answer correct per the gold provisions? [y]es / [n]o / [s]kip / [q]uit: "
            )
            reply = reply.strip().lower()
            if reply in {"y", "n"}:
                append_label(
                    path,
                    Label(
                        record.id,
                        reply == "y",
                        labeller,
                        datetime.now(UTC).isoformat(timespec="seconds"),
                    ),
                )
                added += 1
                break
            if reply == "s":
                break
            if reply == "q":
                return added
    return added
