"""The state machine that runs the agents (ADR-0007).

    PLAN ──▶ RESEARCH ──▶ DRAFT ──▶ VERIFY ──▶ JUDGE ──▶ DONE
                ▲                                │
                └────── research again ◀─────────┘   (contradicted claims, iterations left)

Every transition is explicit and every step has a limit: iterations, a per-step timeout, a
token budget for the run, and a cap on parallel researchers. Each step emits an event, which
the API streams to the client ("Maddələr axtarılır..." → "Yoxlanılır...").

Degradation, not failure: if planning fails, the question itself is the plan; if the
verifier fails, claims count as unverified and confidence drops, so the judge abstains rather
than presenting unchecked claims as checked.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from mizan_agents.agents import confidence, draft, merge_evidence, plan, research, verify
from mizan_agents.gateway import GatewayError, LLMGateway, TokenBudget
from mizan_agents.schemas import (
    AgentAnswer,
    ClaimCheck,
    Draft,
    Evidence,
    Plan,
    ResearchNote,
    SubQuestion,
    Support,
    Verdict,
)
from mizan_agents.tools import LegalTools
from mizan_core.logging import get_logger

log = get_logger(__name__)

ABSTAIN_TEXT = (
    "Bu sual üzrə mənbələrə əsaslanan etibarlı cavab verə bilmirəm. "
    "Sualı dəqiqləşdirin və ya hüquqşünasa müraciət edin."
)
OUT_OF_SCOPE_TEXT = (
    "Bu sual Əmək Məcəlləsinin əhatə dairəsinə aid deyil; mənim mənbələrim ona cavab vermir."
)


class Step(StrEnum):
    PLAN = "plan"
    RESEARCH = "research"
    DRAFT = "draft"
    VERIFY = "verify"
    JUDGE = "judge"
    DONE = "done"


@dataclass(frozen=True, slots=True)
class Event:
    step: Step
    message: str  # shown to the user, Azerbaijani
    detail: dict[str, object] | None = None


EventSink = Callable[[Event], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class Limits:
    max_iterations: int = 2  # research→judge rounds
    step_timeout_s: float = 90.0
    token_budget: int = 40_000
    max_parallel_research: int = 3
    research_k: int = 6
    # Selective prediction: below this confidence the system abstains (calibrated in F5 eval).
    min_confidence: float = 0.5


async def _noop(_: Event) -> None:
    return None


class Orchestrator:
    def __init__(self, gateway: LLMGateway, tools: LegalTools, limits: Limits | None = None):
        self._gateway = gateway
        self._tools = tools
        self.limits = limits or Limits()

    async def _plan(self, question: str, as_of: date, budget: TokenBudget) -> Plan:
        try:
            async with asyncio.timeout(self.limits.step_timeout_s):
                return await plan(self._gateway, question, as_of=as_of, budget=budget)
        except (GatewayError, TimeoutError):
            log.warning("planner_degraded", exc_info=True)
            return Plan(sub_questions=[SubQuestion(text=question)])

    async def _research(self, question: str, p: Plan, as_of: date, k: int) -> list[ResearchNote]:
        """Research the question as asked and each planned sub-question, in parallel.

        The question itself is always researched, so the agent's evidence is never narrower
        than the single-shot baseline's; the planner's rewrites in the Code's terminology add
        what colloquial wording misses.
        """
        gate = asyncio.Semaphore(self.limits.max_parallel_research)
        planned = [s for s in p.sub_questions if s.text.strip() != question.strip()]
        subs = [SubQuestion(text=question), *planned]

        async def one(sub: SubQuestion) -> ResearchNote:
            async with gate, asyncio.timeout(self.limits.step_timeout_s):
                return await research(self._tools, sub, as_of=as_of, k=k)

        return list(await asyncio.gather(*(one(s) for s in subs)))

    async def _verify(
        self, d: Draft, evidence: list[Evidence], budget: TokenBudget
    ) -> list[ClaimCheck]:
        try:
            async with asyncio.timeout(self.limits.step_timeout_s):
                return await verify(self._gateway, d.claims, evidence, budget=budget)
        except (GatewayError, TimeoutError):
            log.warning("verifier_degraded", exc_info=True)
            return [
                ClaimCheck(claim=c, support=Support.NOT_ENOUGH_INFO, quote_found=False)
                for c in d.claims
            ]

    async def run(self, question: str, *, as_of: date, on_event: EventSink = _noop) -> AgentAnswer:
        budget = TokenBudget(self.limits.token_budget)
        trace: list[str] = []

        async def emit(step: Step, message: str, **detail: object) -> None:
            trace.append(step.value)
            await on_event(Event(step, message, detail or None))

        await emit(Step.PLAN, "Sual təhlil edilir...")
        p = await self._plan(question, as_of, budget)
        if not p.in_scope:
            await emit(Step.DONE, "Sual əhatə dairəsindən kənardır.")
            return AgentAnswer(
                question=question,
                as_of=as_of,
                verdict=Verdict.ABSTAIN,
                answer=OUT_OF_SCOPE_TEXT,
                claims=[],
                evidence=[],
                confidence=0.0,
                iterations=0,
                usage=dict(budget.by_role),
                trace=trace,
            )

        k = self.limits.research_k
        feedback = ""
        checks: list[ClaimCheck] = []
        answer_text = ""
        notes: list[ResearchNote] = []
        verdict = Verdict.ABSTAIN
        score = 0.0
        iteration = 0
        for iteration in range(1, self.limits.max_iterations + 1):
            await emit(
                Step.RESEARCH,
                "Maddələr və məhkəmə qərarları axtarılır...",
                sub_questions=[s.text for s in p.sub_questions],
            )
            notes = await self._research(question, p, as_of, k)
            evidence = merge_evidence(notes)
            if not evidence:
                break
            await emit(Step.DRAFT, "Cavab hazırlanır...", sources=[e.key for e in evidence])
            try:
                async with asyncio.timeout(self.limits.step_timeout_s):
                    d = await draft(
                        self._gateway, question, notes, as_of=as_of, budget=budget,
                        feedback=feedback,
                    )  # fmt: skip
            except (GatewayError, TimeoutError):
                log.warning("drafter_failed", exc_info=True)
                break
            await emit(Step.VERIFY, "İddialar mənbələrlə yoxlanılır...", claims=len(d.claims))
            checks = await self._verify(d, evidence, budget)
            answer_text = d.answer
            score = confidence(checks)
            contradicted = [c for c in checks if c.support is Support.CONTRADICTED]
            await emit(Step.JUDGE, "Nəticə qiymətləndirilir...", confidence=score)
            if contradicted and iteration < self.limits.max_iterations:
                verdict = Verdict.RESEARCH_AGAIN
                feedback = "\n".join(
                    f"- Mənbə ilə ziddiyyət: {c.claim.text} ({c.rationale})" for c in contradicted
                )
                k += 3  # look wider the second time
                continue
            verdict = (
                Verdict.ANSWER
                if not contradicted and score >= self.limits.min_confidence
                else Verdict.ABSTAIN
            )
            break

        final_evidence = merge_evidence(notes)
        if verdict is not Verdict.ANSWER:
            verdict = Verdict.ABSTAIN
        await emit(Step.DONE, "Hazırdır." if verdict is Verdict.ANSWER else "Əmin deyiləm.")
        return AgentAnswer(
            question=question,
            as_of=as_of,
            verdict=verdict,
            answer=answer_text if verdict is Verdict.ANSWER else ABSTAIN_TEXT,
            claims=checks,
            evidence=final_evidence,
            confidence=score,
            iterations=iteration,
            usage={**budget.by_role, "total": budget.used},
            trace=trace,
            plan=[s.text for s in p.sub_questions],
        )
