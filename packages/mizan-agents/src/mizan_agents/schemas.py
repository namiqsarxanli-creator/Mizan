"""Everything agents pass to each other is one of these models.

An agent never reads another agent's free text: the planner returns a :class:`Plan`, a
researcher a :class:`ResearchNote`, the drafter a :class:`Draft`, the verifier
:class:`ClaimCheck` objects. Each model is validated at the boundary (``extra="forbid"``), so a
malformed LLM output fails where it happens instead of three steps later.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------------------- planning


class Intent(StrEnum):
    RULE = "rule"  # what does the law say about X
    PROCEDURE = "procedure"  # how / in what order / within what deadline
    EXCEPTION = "exception"  # does X apply to this special case
    TEMPORAL = "temporal"  # what was the rule at a past date


class SubQuestion(Schema):
    text: str = Field(min_length=3, max_length=400)
    intent: Intent = Intent.RULE


class Plan(Schema):
    """The planner's decomposition. One sub-question is a valid plan."""

    sub_questions: list[SubQuestion] = Field(min_length=1, max_length=4)
    in_scope: bool = True  # False: not a Labour Code question; the system abstains early


# ---------------------------------------------------------------------------- research


class EvidenceKind(StrEnum):
    ARTICLE = "article"
    DECISION = "decision"  # Constitutional Court


class Evidence(Schema):
    key: str  # "art:69", "kmq:61412"
    title: str
    text: str
    kind: EvidenceKind = EvidenceKind.ARTICLE
    in_force_since: date
    via: str = "search"  # which tool found it: search / graph / decisions / cited


class ResearchNote(Schema):
    sub_question: SubQuestion
    evidence: list[Evidence]
    conflicts: list[str] = Field(default_factory=list)  # human-readable collision resolutions


# ---------------------------------------------------------------------------- drafting


class Claim(Schema):
    """One legal statement of the draft, with the evidence it rests on."""

    text: str = Field(min_length=3)
    source: str  # evidence key
    quote: str = Field(min_length=3)  # verbatim from that evidence


class Draft(Schema):
    answer: str
    claims: list[Claim] = Field(default_factory=list)


# ---------------------------------------------------------------------------- verification


class Support(StrEnum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    NOT_ENOUGH_INFO = "not_enough_info"


class ClaimCheck(Schema):
    claim: Claim
    support: Support
    quote_found: bool  # mechanical: the quote occurs verbatim in the named evidence
    rationale: str = ""


class Verdict(StrEnum):
    ANSWER = "answer"
    ABSTAIN = "abstain"
    RESEARCH_AGAIN = "research_again"


class AgentAnswer(Schema):
    question: str
    as_of: date
    verdict: Verdict
    answer: str
    claims: list[ClaimCheck]
    evidence: list[Evidence]
    confidence: float = Field(ge=0.0, le=1.0)
    iterations: int
    usage: dict[str, int] = Field(default_factory=dict)
    trace: list[str] = Field(default_factory=list)  # step names, for SSE and debugging
    plan: list[str] = Field(default_factory=list)  # the planner's sub-questions
