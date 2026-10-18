from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mizan_evals.judge import GroqJudge, JudgeError, build_prompt, parse_verdict
from mizan_evals.labels import (
    Label,
    append_label,
    cohens_kappa,
    label_session,
    load_labels,
    render_item,
)
from mizan_evals.run import (
    Record,
    cites_gold,
    load_records,
    run_generation_eval,
    stratified_sample,
    summarise,
)
from mizan_retrieval.answer import Answer, AnswerUnavailableError, Citation
from mizan_retrieval.evaluation import Question
from mizan_retrieval.search import Hit, SearchConfig

TODAY = date(2026, 10, 16)


def q(id_: str, qtype: str = "single", relevant: Sequence[str] = ("art:69",)) -> Question:
    return Question(id_, f"sual {id_}", frozenset(relevant), qtype, TODAY)


# ----------------------------------------------------------------------------------- judge


def test_prompt_uses_type_specific_rules() -> None:
    no_answer = build_prompt(question="?", qtype="no_answer", as_of=TODAY, gold="", answer="x")
    premise = build_prompt(question="?", qtype="false_premise", as_of=TODAY, gold="g", answer="x")
    default = build_prompt(question="?", qtype="single", as_of=TODAY, gold="g", answer="x")

    assert "OUTSIDE the scope" in no_answer
    assert "no provision of the Labour Code" in no_answer
    assert "rejects the premise" in premise
    assert "legal conclusion agrees" in default
    assert "2026-10-16" in default


def test_verdict_parsing() -> None:
    v = parse_verdict('{"correct": true, "abstained": false, "reason": "ok"}', "m")
    assert (v.correct, v.abstained, v.reason, v.model) == (True, False, "ok", "m")
    with pytest.raises(JudgeError):
        parse_verdict("not json", "m")


class ScriptedCompletions:
    def __init__(self, *replies: str | Exception) -> None:
        self.replies = list(replies)
        self.calls = 0

    async def create(self, **_: Any) -> Any:
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])


def groq_client(*replies: str | Exception) -> tuple[Any, ScriptedCompletions]:
    completions = ScriptedCompletions(*replies)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


async def test_judge_retries_malformed_output(monkeypatch: pytest.MonkeyPatch) -> None:
    client, completions = groq_client("garbage", '{"correct": false, "reason": "wrong article"}')

    verdict = await GroqJudge(client).judge(
        question="?", qtype="single", as_of=TODAY, gold="g", answer="a"
    )

    assert verdict.correct is False
    assert completions.calls == 2


async def test_judge_gives_up_eventually(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = groq_client("x", "y", "z")
    with pytest.raises(JudgeError, match="after 3 attempts"):
        await GroqJudge(client).judge(question="?", qtype="single", as_of=TODAY, gold="", answer="")


# ------------------------------------------------------------------------------------- run


def test_stratified_sample_keeps_every_type() -> None:
    questions = [q(f"s{i}") for i in range(80)] + [q(f"n{i}", "no_answer", ()) for i in range(6)]

    sample = stratified_sample(questions, 20)

    assert {x.type for x in sample} == {"single", "no_answer"}
    assert sum(x.type == "no_answer" for x in sample) >= 2
    assert sample == stratified_sample(questions, 20)  # seeded, reproducible


def test_cites_gold_matches_at_article_level() -> None:
    assert cites_gold([{"key": "art:69/part:1", "quote": "x"}], ["art:69"]) is True
    assert cites_gold([{"key": "art:77", "quote": "x"}], ["art:69"]) is False
    assert cites_gold([], []) is None


HIT = Hit("art:69", "art:69", "art:69", "Maddə 69\nmətn", 0.9, TODAY, ("art:69",))


class FakeSearcher:
    async def search(
        self,
        query: str,
        *,
        source_id: int,
        as_of: date,
        config: SearchConfig,
        k: int = 10,
        only_articles: Collection[str] | None = None,
    ) -> list[Hit]:
        return [HIT]


class FakeAnswerer:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.calls = 0
        self.fail_first = fail_first

    async def answer(self, question: str, hits: Sequence[Hit], *, as_of: date) -> Answer:
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise AnswerUnavailableError("rate-limited", retry_after_s=0)
        citation = Citation("art:77", "Maddə 77", "iki həftə")  # cites the wrong article
        return Answer("cavab", (citation,), "m", "stop", rejected_citations=1)


async def _expand(hits: Sequence[Hit], on: date) -> list[Hit]:
    return list(hits)


async def _gold(keys: Sequence[str], on: date) -> str:
    return "gold text"


async def test_generation_eval_records_and_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("mizan_evals.run.asyncio.sleep", _no_sleep)
    out = tmp_path / "records.jsonl"
    client, completions = groq_client(
        '{"correct": false, "abstained": false, "reason": "cites art 77"}',
        '{"correct": true, "abstained": true, "reason": "declined"}',
    )
    answerer = FakeAnswerer(fail_first=True)
    judge = GroqJudge(client)

    async def run(questions: list[Question]) -> dict[str, Record]:
        return await run_generation_eval(
            questions,
            searcher=FakeSearcher(),
            config=SearchConfig(),
            expand=_expand,
            answerer=answerer,
            judge=judge,
            gold_text=_gold,
            source_id=46943,
            out=out,
        )

    questions = [q("a"), q("b", "no_answer", ())]

    records = await run(questions)

    a, b = records["a"], records["b"]
    assert answerer.calls == 3  # one retry after the rate limit
    assert (a.cites_gold, a.citation_valid, a.correct) == (False, 0.5, False)
    assert (b.cites_gold, b.correct, b.abstained) == (None, True, True)
    assert set(load_records(out)) == {"a", "b"}

    # A second run finds every question done and calls nothing.
    await run(questions)
    assert completions.calls == 2


async def _no_sleep(_: float) -> None:
    return None


def test_summary_separates_scope_and_abstention() -> None:
    records = [
        Record("a", "single", "2026-10-16", "?", ["art:1"], correct=True, cites_gold=True),
        Record("b", "single", "2026-10-16", "?", ["art:1"], correct=False, cites_gold=False),
        Record("c", "no_answer", "2026-10-16", "?", [], correct=True),
        Record("d", "single", "2026-10-16", "?", ["art:1"], error="boom"),
    ]
    s = summarise(records)
    assert s["failed"] == 1
    assert s["answer_correct_in_scope"]["mean"] == 0.5
    assert s["abstention_accuracy"]["mean"] == 1.0
    assert s["cites_gold"]["mean"] == 0.5


# ---------------------------------------------------------------------------------- labels


def test_kappa_known_values() -> None:
    # Perfect agreement.
    assert cohens_kappa([(True, True), (False, False)] * 5).kappa == 1.0
    # Agreement no better than chance.
    chance = cohens_kappa([(True, True), (True, False), (False, True), (False, False)])
    assert chance.kappa == pytest.approx(0.0)
    # Textbook example: 20 yes/yes, 5 yes/no, 10 no/yes, 15 no/no -> kappa = 0.4
    pairs = [(True, True)] * 20 + [(True, False)] * 5 + [(False, True)] * 10 + [(False, False)] * 15
    result = cohens_kappa(pairs)
    assert result.kappa == pytest.approx(0.4)
    assert result.interpretation == "fair"  # Landis & Koch: 0.21-0.40 is "fair"
    assert result.confusion["correct"]["incorrect"] == 5


def test_labelling_is_blind_and_resumable(tmp_path: Path) -> None:
    path = tmp_path / "labels.jsonl"
    secret = "JUDGE-REASON-SHOULD-NOT-BE-SHOWN"
    records = {
        i: Record(
            i,
            "single",
            "2026-10-16",
            f"sual {i}",
            ["art:69"],
            answer="cavab",
            correct=True,
            reason=secret,
        )
        for i in ("a", "b", "c")
    }
    shown: list[str] = []
    replies = iter(["y", "?", "n", "q"])

    added = label_session(
        records,
        path,
        labeller="tester",
        gold_text=lambda r: "gold",
        ask=lambda _: next(replies),
        show=shown.append,
    )

    assert added == 2
    assert all(secret not in s for s in shown)
    assert {k: v.correct for k, v in load_labels(path).items()} == {"a": True, "b": False}
    # Resuming starts at the first unlabelled record.
    shown.clear()
    label_session(
        records,
        path,
        labeller="tester",
        gold_text=lambda r: "gold",
        ask=lambda _: "q",
        show=shown.append,
    )
    assert "c  type=" in shown[0]


def test_later_labels_override_earlier_ones(tmp_path: Path) -> None:
    path = tmp_path / "labels.jsonl"
    append_label(path, Label("a", True, "x", "t1"))
    append_label(path, Label("a", False, "x", "t2"))
    assert load_labels(path)["a"].correct is False


def test_render_item_shows_out_of_scope_gold() -> None:
    text = render_item(Record("z", "no_answer", "2026-10-16", "?", []), "")
    assert "none: out of scope" in text
