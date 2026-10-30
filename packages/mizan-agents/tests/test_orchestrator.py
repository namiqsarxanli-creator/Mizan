"""The agent state machine end to end, with a scripted LLM and in-memory tools."""

import asyncio
import json
from collections.abc import Collection, Sequence
from datetime import date
from types import SimpleNamespace
from typing import Any

from temporalio import activity

from mizan_agents.agents import confidence, quote_in, research
from mizan_agents.durable import DraftIn, DraftOut
from mizan_agents.gateway import LLMGateway
from mizan_agents.orchestrator import ABSTAIN_TEXT, Event, Limits, Orchestrator, Step
from mizan_agents.schemas import (
    AgentAnswer,
    Claim,
    ClaimCheck,
    Evidence,
    SubQuestion,
    Support,
    Verdict,
)
from mizan_agents.tools import LegalTools
from mizan_graph.build import Reference
from mizan_graph.citations import SELF
from mizan_graph.decisions import Decision
from mizan_graph.store import InMemoryGraph
from mizan_ingestion.chunking import Strategy
from mizan_retrieval.corpus import OPEN_END
from mizan_retrieval.search import Hit, Mode, SearchConfig

TODAY = date(2026, 10, 25)
TEXTS = {
    "art:51": "Kod > Maddə 51. Sınaq müddəti\nBu Məcəllənin 52-ci maddəsində göstərilən hallar "
    "istisna olmaqla, sınaq müddəti ən çoxu üç ay müəyyən edilir.",
    "art:52": "Kod > Maddə 52. Sınaq müddəti müəyyən edilməyən hallar\nYaşı 18-dən az olan "
    "şəxslərə sınaq müddəti müəyyən edilmir.",
    "art:144": "Kod > Maddə 144. Kompensasiya\nƏlavə məzuniyyətlərə görə kompensasiya verilmir.",
}
GRAPH = InMemoryGraph(
    46943,
    [Reference("art:51", SELF, "art:52", ("52",), date(1999, 2, 1), OPEN_END, exception=True)],
    [
        Decision(
            61412,
            "Əmək Məcəlləsinin 144-cü maddəsinin ... uyğunluğunun yoxlanılmasına dair",
            date(2026, 2, 18),
            "unconstitutional",
            "1. ... tətbiqi dayandırılsın.",
            ("art:144",),
        )
    ],
)


def hit(key: str) -> Hit:
    return Hit(key, key, key, TEXTS[key], 0.8, date(2021, 5, 27), (key,))


class Searcher:
    def __init__(self, keys: list[str]) -> None:
        self.keys = keys
        self.ks: list[int] = []

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
        self.ks.append(k)
        return [hit(key) for key in self.keys][:k]


async def expand(hits: Sequence[Hit], source_id: int, as_of: date) -> list[Hit]:
    return [hit(h.article_key) if not h.text else h for h in hits if h.article_key in TEXTS]


def tools(keys: list[str]) -> LegalTools:
    config = SearchConfig(Mode.DENSE, strategy=Strategy.ARTICLE)
    return LegalTools(Searcher(keys), config, expand, GRAPH, GRAPH)


def reply(payload: object) -> Any:
    message = SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=50, completion_tokens=50),
    )


class RoleLLM:
    """Answers by role (recognised from the system prompt); scripts can be changed per call."""

    def __init__(self, *, plan: object, drafts: list[object], checks: list[object]) -> None:
        self.plan, self.drafts, self.checks = plan, drafts, checks
        self.chat = SimpleNamespace(completions=self)
        self.calls: list[str] = []

    async def create(self, **request: Any) -> Any:
        system = request["messages"][0]["content"]
        if "planlaşdırıcı" in system:
            self.calls.append("planner")
            if isinstance(self.plan, BaseException):
                raise self.plan
            return reply(self.plan)
        if "yoxlayıcı" in system:
            self.calls.append("verifier")
            outcome = self.checks.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return reply(outcome)
        self.calls.append("drafter")
        return reply(self.drafts.pop(0))


PLAN = {"sub_questions": [{"text": "Sınaq müddəti nə qədərdir?", "intent": "rule"}]}
GOOD_DRAFT = {
    "answer": "Sınaq müddəti ən çoxu üç aydır; 18 yaşına çatmayanlara qoyulmur.",
    "claims": [
        {"text": "ən çoxu üç ay", "source": "art:51", "quote": "ən çoxu üç ay"},
        {"text": "azyaşlılara qoyulmur", "source": "art:52", "quote": "sınaq müddəti müəyyən"},
    ],
}
SUPPORTED = {"checks": [{"i": 1, "support": "supported"}, {"i": 2, "support": "supported"}]}


def orchestrator(llm: RoleLLM, keys: list[str], **limits: Any) -> Orchestrator:
    async def no_sleep(_: float) -> None:
        return None

    return Orchestrator(LLMGateway(llm, sleep=no_sleep), tools(keys), Limits(**limits))


async def test_answers_with_verified_claims_and_streams_each_step() -> None:
    llm = RoleLLM(plan=PLAN, drafts=[GOOD_DRAFT], checks=[SUPPORTED])
    events: list[Event] = []

    async def sink(event: Event) -> None:
        events.append(event)

    result = await orchestrator(llm, ["art:51"]).run("Sınaq müddəti?", as_of=TODAY, on_event=sink)

    assert result.verdict is Verdict.ANSWER
    assert result.confidence == 1.0
    assert [e.step for e in events] == [
        Step.PLAN, Step.RESEARCH, Step.DRAFT, Step.VERIFY, Step.JUDGE, Step.DONE,
    ]  # fmt: skip
    # The researcher followed the exception edge from Art. 51 to Art. 52.
    assert [(e.key, e.via) for e in result.evidence] == [("art:51", "search"), ("art:52", "graph")]
    assert llm.calls == ["planner", "drafter", "verifier"]
    assert result.usage["total"] == 300


async def test_contradicted_claims_trigger_a_wider_second_round() -> None:
    contradicted = {"checks": [{"i": 1, "support": "contradicted", "rationale": "6 ay deyil"}]}
    llm = RoleLLM(
        plan=PLAN,
        drafts=[
            {"answer": "6 ay", "claims": [{"text": "6 ay", "source": "art:51", "quote": "üç ay"}]},
            GOOD_DRAFT,
        ],
        checks=[contradicted, SUPPORTED],
    )
    o = orchestrator(llm, ["art:51"])
    result = await o.run("Sınaq müddəti?", as_of=TODAY)

    assert result.verdict is Verdict.ANSWER
    assert result.iterations == 2
    searcher = o._tools.searcher
    assert isinstance(searcher, Searcher)
    # The question and the planned sub-question, each searched again 3 deeper.
    assert searcher.ks == [6, 6, 9, 9]


async def test_still_contradicted_after_the_last_round_abstains() -> None:
    contradicted = {"checks": [{"i": 1, "support": "contradicted"}]}
    draft = {"answer": "x", "claims": [{"text": "x", "source": "art:51", "quote": "üç ay"}]}
    llm = RoleLLM(plan=PLAN, drafts=[draft, draft], checks=[contradicted, contradicted])
    result = await orchestrator(llm, ["art:51"]).run("Sınaq müddəti?", as_of=TODAY)
    assert (result.verdict, result.answer) == (Verdict.ABSTAIN, ABSTAIN_TEXT)


async def test_out_of_scope_questions_abstain_before_research() -> None:
    llm = RoleLLM(plan={**PLAN, "in_scope": False}, drafts=[], checks=[])
    result = await orchestrator(llm, ["art:51"]).run("Vizanı necə almaq olar?", as_of=TODAY)
    assert result.verdict is Verdict.ABSTAIN
    assert llm.calls == ["planner"]


async def test_planner_failure_degrades_to_the_question_itself() -> None:
    llm = RoleLLM(plan=ValueError("boom"), drafts=[GOOD_DRAFT], checks=[SUPPORTED])
    result = await orchestrator(llm, ["art:51"]).run("Sınaq müddəti?", as_of=TODAY)
    assert result.verdict is Verdict.ANSWER


async def test_verifier_failure_means_unverified_and_abstains() -> None:
    llm = RoleLLM(plan=PLAN, drafts=[GOOD_DRAFT], checks=[ValueError("down")])
    result = await orchestrator(llm, ["art:51"]).run("Sınaq müddəti?", as_of=TODAY)
    assert result.verdict is Verdict.ABSTAIN
    assert all(c.support is Support.NOT_ENOUGH_INFO for c in result.claims)


async def test_research_adds_court_decisions_and_reports_the_suspension() -> None:
    note = await research(tools(["art:144"]), SubQuestion(text="kompensasiya"), as_of=TODAY)
    assert [e.key for e in note.evidence] == ["art:144", "kmq:61412"]
    assert any("constitutional court" in c and "art:144" in c for c in note.conflicts)

    before = await research(
        tools(["art:144"]), SubQuestion(text="kompensasiya"), as_of=date(2025, 12, 1)
    )
    assert [e.key for e in before.evidence] == ["art:144"]
    assert before.conflicts == []


def test_quote_check_is_whitespace_and_case_insensitive() -> None:
    e = Evidence(key="art:51", title="t", text="Sınaq  müddəti ən çoxu ÜÇ ay", in_force_since=TODAY)
    assert quote_in("sınaq müddəti ən çoxu üç ay", e)
    assert not quote_in("altı ay", e)
    assert not quote_in("üç ay", None)


def test_confidence_rewards_verbatim_support() -> None:
    claim = Claim(text="iddia", source="art:51", quote="xyz")
    checks = [
        ClaimCheck(claim=claim, support=Support.SUPPORTED, quote_found=True),
        ClaimCheck(claim=claim, support=Support.SUPPORTED, quote_found=False),
        ClaimCheck(claim=claim, support=Support.NOT_ENOUGH_INFO, quote_found=True),
    ]
    assert confidence(checks) == round((1 + 0.6 + 0) / 3, 3)
    assert confidence([]) == 0.0


def test_elided_quotes_count_when_every_fragment_occurs_in_order() -> None:
    e = Evidence(
        key="art:62",
        title="t",
        text="İşəgötürən aşağıdakı hallarda işçini müvafiq iş vaxtı ərzində işdən kənar edə bilər:",
        in_force_since=TODAY,
    )
    assert quote_in("İşəgötürən aşağıdakı hallarda ... işdən kənar edə bilər:", e)
    assert quote_in("İşəgötürən … işdən kənar edə bilər", e)
    assert not quote_in("işdən kənar edə bilər ... İşəgötürən", e)  # order matters
    assert not quote_in("İşəgötürən ... işdən azad edə bilər", e)


# ---------------------------------------------------------------------- durable (Temporal)
# The same scripted runs through the Temporal workflow, on a local Temporal dev server (the SDK
# downloads it once).


async def _durable_run(
    llm: RoleLLM, *, hang_first_draft: bool = False
) -> tuple[AgentAnswer, list[str]]:
    from temporalio.client import Client
    from temporalio.contrib.pydantic import pydantic_data_converter
    from temporalio.runtime import Runtime, TelemetryConfig
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from mizan_agents.durable import AgentActivities, AgentRun, DurableAgent

    agent = orchestrator(llm, ["art:51"])
    events: list[str] = []

    async def sink(event: Event) -> None:
        events.append(event.step.value)

    class Hanging(AgentActivities):
        """A worker that dies in the middle of drafting."""

        @activity.defn(name="draft")
        async def draft(self, inp: DraftIn) -> DraftOut:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    started = asyncio.Event()
    env = await WorkflowEnvironment.start_local(data_converter=pydantic_data_converter)
    async with env:
        durable = DurableAgent(env.client, task_queue="test", poll_s=0.05, heartbeat_s=2)
        if not hang_first_draft:
            async with Worker(
                env.client, task_queue="test", workflows=[AgentRun],
                activities=AgentActivities(agent).all(),
            ):  # fmt: skip
                first_answer = await durable.run(
                    "Sınaq müddəti?", as_of=TODAY, on_event=sink, request_id="r1"
                )
                calls = list(llm.calls)
                # The same request again: the finished run's answer, no new LLM calls.
                again = await durable.run("Sınaq müddəti?", as_of=TODAY, request_id="r1")
                assert (again, llm.calls) == (first_answer, calls)
                return first_answer, events
        # Another worker process: its own client and SDK runtime, as a real deployment would be.
        other = await Client.connect(
            env.client.service_client.config.target_host,
            data_converter=pydantic_data_converter,
            runtime=Runtime(telemetry=TelemetryConfig()),
        )
        first = Worker(
            other, task_queue="test", workflows=[AgentRun],
            activities=Hanging(agent).all(),
        )  # fmt: skip
        first_task = asyncio.create_task(first.run())
        run = asyncio.create_task(durable.run("Sınaq müddəti?", as_of=TODAY, on_event=sink))
        await started.wait()
        first_task.cancel()  # the worker is gone mid-draft
        async with Worker(
            env.client, task_queue="test", workflows=[AgentRun],
            activities=AgentActivities(agent).all(),
        ):  # fmt: skip
            return await run, events


async def test_durable_run_matches_the_in_process_run() -> None:
    local = await orchestrator(
        RoleLLM(plan=PLAN, drafts=[GOOD_DRAFT], checks=[SUPPORTED]), ["art:51"]
    ).run("Sınaq müddəti?", as_of=TODAY)
    llm = RoleLLM(plan=PLAN, drafts=[GOOD_DRAFT], checks=[SUPPORTED])
    durable, events = await _durable_run(llm)

    assert durable == local
    assert events == ["plan", "research", "draft", "verify", "judge", "done"]
    assert llm.calls == ["planner", "drafter", "verifier"]


async def test_durable_run_survives_a_worker_dying_mid_step() -> None:
    llm = RoleLLM(plan=PLAN, drafts=[GOOD_DRAFT], checks=[SUPPORTED])
    result, _ = await _durable_run(llm, hang_first_draft=True)

    assert result.verdict is Verdict.ANSWER
    # The plan finished before the crash and is not paid for twice; drafting is redone.
    assert llm.calls == ["planner", "drafter", "verifier"]
    assert result.usage["total"] == 300
