"""Agent vs baseline eval harness: records, resumption, metrics."""

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from mizan_evals.agent_eval import (
    AgentRecord,
    SystemOutput,
    load_agent_records,
    run_agent_eval,
    summarise_agent_eval,
)
from mizan_evals.judge import Verdict as JudgeVerdict
from mizan_retrieval.evaluation import Question

Q = [
    Question(f"q{i}", "sual?", frozenset({"art:69"}), "single", date(2026, 10, 1))
    for i in range(1, 5)
]


class Judge:
    async def judge(
        self, *, question: str, qtype: str, as_of: date, gold: str, answer: str
    ) -> JudgeVerdict:
        return JudgeVerdict(correct="düz" in answer, abstained=False, reason="", model="fake")


class Faith:
    def __init__(self) -> None:
        self.calls = 0

    async def judge(
        self, answer: str, sources: Sequence[tuple[str, str]]
    ) -> tuple[float, list[str]]:
        self.calls += 1
        return (1.0, []) if "düz" in answer else (0.5, ["uydurma"])


def system(answers: dict[str, str], *, abstain: frozenset[str] = frozenset()) -> Any:
    async def run(q: Question) -> SystemOutput:
        return SystemOutput(
            answer=answers[q.id],
            abstained=q.id in abstain,
            citations_total=2,
            citations_verbatim=2 if "düz" in answers[q.id] else 1,
            sources=[("art:69", "mətn")],
            tokens=100,
        )

    return run


async def gold(_: Question) -> str:
    return "gold"


async def test_eval_records_both_systems_and_summarises(tmp_path: Path) -> None:
    out = tmp_path / "records.jsonl"
    faith = Faith()
    systems = {
        "baseline": system({"q1": "düz", "q2": "səhv", "q3": "səhv", "q4": "səhv"}),
        "agent": system(
            {"q1": "düz", "q2": "düz", "q3": "düz", "q4": "bilmirəm"}, abstain=frozenset({"q4"})
        ),
    }

    records = await run_agent_eval(
        Q,
        systems,
        judge=Judge(),
        faithfulness=faith,
        gold=gold,
        out=out,
    )

    assert len(records) == 8
    assert faith.calls == 7  # no faithfulness check for the abstention
    summary = summarise_agent_eval(list(records.values()), ["baseline", "agent"])
    assert summary["agent"]["coverage"]["mean"] == 0.75
    assert summary["agent"]["selective_accuracy"]["mean"] == 1.0
    assert summary["agent"]["faithfulness"]["mean"] == 1.0
    assert summary["baseline"]["citation_accuracy"]["mean"] == round((1 + 0.5 * 3) / 4, 3)
    assert summary["paired_correct"]["delta"] == 0.5  # agent 3/4 correct vs baseline 1/4

    # Resumable: a second run reuses every finished record.
    again = await run_agent_eval(
        Q,
        systems,
        judge=Judge(),
        faithfulness=faith,
        gold=gold,
        out=out,
    )
    assert faith.calls == 7
    assert set(load_agent_records(out)) == set(again)


def test_citation_ok_needs_citations() -> None:
    r = AgentRecord("agent", "q1", "single", "2026-10-01", "sual?")
    assert r.citation_ok is None
