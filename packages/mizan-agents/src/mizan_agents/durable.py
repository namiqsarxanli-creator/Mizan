"""Durable agent runs on Temporal: a run survives the worker or the API restarting.

The workflow runs the same state machine as the in-process agent (``run_steps``). Each step is
a Temporal activity, and its result is recorded in the workflow history. A worker that dies
mid-run is replaced; the next worker replays the history and continues from the step that was
running, without repeating the LLM calls that already finished (they cost tokens and are not
deterministic).

- **Activities** wrap the in-process steps of an ``Orchestrator``. Timeouts, tracing and
  degradation stay in one place. The token budget crosses the activity boundary as "tokens
  left"; each activity returns what it spent, by role.
- **Heartbeats:** a running step reports every few seconds, so a worker that dies is noticed
  after ``heartbeat_s`` instead of after the step timeout.
- **Retries:** an activity is retried when its worker dies or it raises. When retries run out,
  the step degrades exactly as in process: plan falls back to the question, verify marks
  claims unverified, a failed draft ends the run with an abstention.
- **Idempotency:** the workflow id is the caller's request id. A repeated request attaches to
  the run already in progress or finished, instead of paying for a second one.

    API  ──start/attach──▶  Temporal  ◀──poll──  agent worker (``mizan-agent-worker``)
                                                  └─ activities: plan, research, draft, verify
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, Field
from temporalio import activity, workflow
from temporalio.client import WorkflowQueryFailedError
from temporalio.common import RetryPolicy, WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ActivityError, WorkflowAlreadyStartedError
from temporalio.service import RPCError

with workflow.unsafe.imports_passed_through():
    from mizan_agents.gateway import Role, TokenBudget
    from mizan_agents.orchestrator import (
        Event,
        EventSink,
        Limits,
        Orchestrator,
        Step,
        _noop,
        run_steps,
    )
    from mizan_agents.schemas import (
        AgentAnswer,
        ClaimCheck,
        Draft,
        Evidence,
        Plan,
        ResearchNote,
        SubQuestion,
        Support,
    )

TASK_QUEUE = "mizan-agent"
WORKFLOW = "AgentRun"


class AgentInput(BaseModel):
    question: str
    as_of: date
    limits: Limits = Field(default_factory=Limits)
    heartbeat_s: float = 10.0


class PlanIn(BaseModel):
    question: str
    as_of: date
    budget_left: int


class PlanOut(BaseModel):
    plan: Plan
    usage: dict[str, int]


class ResearchIn(BaseModel):
    question: str
    plan: Plan
    as_of: date
    k: int


class DraftIn(BaseModel):
    question: str
    notes: list[ResearchNote]
    as_of: date
    budget_left: int
    feedback: str
    iteration: int


class DraftOut(BaseModel):
    draft: Draft | None
    usage: dict[str, int]


class VerifyIn(BaseModel):
    draft: Draft
    evidence: list[Evidence]
    budget_left: int


class VerifyOut(BaseModel):
    checks: list[ClaimCheck]
    usage: dict[str, int]


def _budget(left: int) -> TokenBudget:
    return TokenBudget(max(left, 0))


async def _heartbeating[T](step: Awaitable[T]) -> T:
    """Run a step while telling Temporal, a few times per heartbeat timeout, that we are alive."""
    timeout = activity.info().heartbeat_timeout
    every = timeout.total_seconds() / 3 if timeout else 5.0
    task = asyncio.ensure_future(step)
    while True:
        done, _ = await asyncio.wait({task}, timeout=every)
        if done:
            return task.result()
        activity.heartbeat()


class AgentActivities:
    """The steps of an in-process ``Orchestrator``, exposed as Temporal activities."""

    def __init__(self, agent: Orchestrator) -> None:
        self._agent = agent

    @activity.defn(name="plan")
    async def plan(self, inp: PlanIn) -> PlanOut:
        budget = _budget(inp.budget_left)
        p = await _heartbeating(self._agent.plan(inp.question, inp.as_of, budget))
        return PlanOut(plan=p, usage=dict(budget.by_role))

    @activity.defn(name="research")
    async def research(self, inp: ResearchIn) -> list[ResearchNote]:
        return await _heartbeating(self._agent.research(inp.question, inp.plan, inp.as_of, inp.k))

    @activity.defn(name="draft")
    async def draft(self, inp: DraftIn) -> DraftOut:
        budget = _budget(inp.budget_left)
        d = await _heartbeating(
            self._agent.draft(
                inp.question, inp.notes, inp.as_of, budget, inp.feedback, inp.iteration
            )
        )
        return DraftOut(draft=d, usage=dict(budget.by_role))

    @activity.defn(name="verify")
    async def verify(self, inp: VerifyIn) -> VerifyOut:
        budget = _budget(inp.budget_left)
        checks = await _heartbeating(self._agent.verify(inp.draft, inp.evidence, budget))
        return VerifyOut(checks=checks, usage=dict(budget.by_role))

    def all(self) -> list[Any]:
        return [self.plan, self.research, self.draft, self.verify]


def _charge(budget: TokenBudget, usage: dict[str, int]) -> None:
    for role, tokens in usage.items():
        budget.charge(Role(role), tokens)


class _ActivitySteps:
    """``Steps`` for the workflow: every step is an activity call."""

    def __init__(self, limits: Limits, heartbeat_s: float) -> None:
        # The in-process step timeout, plus room for the activity to report back.
        self._options: dict[str, Any] = {
            "start_to_close_timeout": timedelta(seconds=limits.step_timeout_s + 30),
            "heartbeat_timeout": timedelta(seconds=heartbeat_s),
            "retry_policy": RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=2)),
        }

    async def plan(self, question: str, as_of: date, budget: TokenBudget) -> Plan:
        inp = PlanIn(question=question, as_of=as_of, budget_left=budget.limit - budget.used)
        try:
            out: PlanOut = await workflow.execute_activity(
                "plan", inp, result_type=PlanOut, **self._options
            )
        except ActivityError:
            workflow.logger.warning("planner_degraded")
            return Plan(sub_questions=[SubQuestion(text=question)])
        _charge(budget, out.usage)
        return out.plan

    async def research(self, question: str, p: Plan, as_of: date, k: int) -> list[ResearchNote]:
        inp = ResearchIn(question=question, plan=p, as_of=as_of, k=k)
        notes: list[ResearchNote] = await workflow.execute_activity(
            "research", inp, result_type=list[ResearchNote], **self._options
        )
        return notes

    async def draft(
        self,
        question: str,
        notes: list[ResearchNote],
        as_of: date,
        budget: TokenBudget,
        feedback: str,
        iteration: int,
    ) -> Draft | None:
        inp = DraftIn(
            question=question,
            notes=notes,
            as_of=as_of,
            budget_left=budget.limit - budget.used,
            feedback=feedback,
            iteration=iteration,
        )
        try:
            out: DraftOut = await workflow.execute_activity(
                "draft", inp, result_type=DraftOut, **self._options
            )
        except ActivityError:
            workflow.logger.warning("drafter_failed")
            return None
        _charge(budget, out.usage)
        return out.draft

    async def verify(
        self, d: Draft, evidence: list[Evidence], budget: TokenBudget
    ) -> list[ClaimCheck]:
        inp = VerifyIn(draft=d, evidence=evidence, budget_left=budget.limit - budget.used)
        try:
            out: VerifyOut = await workflow.execute_activity(
                "verify", inp, result_type=VerifyOut, **self._options
            )
        except ActivityError:
            workflow.logger.warning("verifier_degraded")
            return [
                ClaimCheck(claim=c, support=Support.NOT_ENOUGH_INFO, quote_found=False)
                for c in d.claims
            ]
        _charge(budget, out.usage)
        return out.checks


class StepEvent(BaseModel):
    step: Step
    message: str
    detail: dict[str, Any] | None = None


@workflow.defn(name=WORKFLOW)
class AgentRun:
    def __init__(self) -> None:
        self._events: list[StepEvent] = []

    @workflow.run
    async def run(self, inp: AgentInput) -> AgentAnswer:
        async def record(event: Event) -> None:
            self._events.append(
                StepEvent(step=event.step, message=event.message, detail=event.detail)
            )

        return await run_steps(
            _ActivitySteps(inp.limits, inp.heartbeat_s),
            inp.question,
            as_of=inp.as_of,
            limits=inp.limits,
            on_event=record,
        )

    @workflow.query
    def progress(self) -> list[StepEvent]:
        return self._events


class DurableAgent:
    """The API side: the same ``run`` as ``Orchestrator``, executed by a Temporal worker.

    Events reach ``on_event`` by polling the workflow's ``progress`` query, so streaming
    (``/v1/agent/ask/stream``) works unchanged.
    """

    def __init__(
        self,
        client: Any,  # temporalio.client.Client
        *,
        task_queue: str = TASK_QUEUE,
        limits: Limits | None = None,
        poll_s: float = 0.5,
        heartbeat_s: float = 10.0,
    ) -> None:
        self._client = client
        self._task_queue = task_queue
        self.limits = limits or Limits()
        self._poll_s = poll_s
        self._heartbeat_s = heartbeat_s

    async def run(
        self,
        question: str,
        *,
        as_of: date,
        on_event: EventSink = _noop,
        request_id: str | None = None,
    ) -> AgentAnswer:
        workflow_id = f"agent-{request_id or uuid.uuid4()}"
        try:
            handle = await self._client.start_workflow(
                WORKFLOW,
                AgentInput(
                    question=question,
                    as_of=as_of,
                    limits=self.limits,
                    heartbeat_s=self._heartbeat_s,
                ),
                id=workflow_id,
                task_queue=self._task_queue,
                result_type=AgentAnswer,
                # A retried request joins the run it already started, while it runs ...
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                # ... and gets its answer once it has finished. Only a failed run starts again.
                id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
            )
        except WorkflowAlreadyStartedError:
            handle = self._client.get_workflow_handle(workflow_id, result_type=AgentAnswer)
        result = asyncio.ensure_future(handle.result())
        seen = 0
        while True:
            done = result.done()
            try:
                events = await handle.query(AgentRun.progress, result_type=list[StepEvent])
            except (WorkflowQueryFailedError, RPCError):  # a worker may be restarting
                events = []
            for e in events[seen:]:
                await on_event(Event(e.step, e.message, e.detail))
            seen = max(seen, len(events))
            if done:
                answer: AgentAnswer = await result
                return answer
            await asyncio.wait({result}, timeout=self._poll_s)
