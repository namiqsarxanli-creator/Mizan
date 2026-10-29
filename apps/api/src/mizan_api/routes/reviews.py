"""Human-in-the-loop review queue (F9): answers a person should check, and their decisions."""

from __future__ import annotations

import hmac
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, Field

from mizan_api.quality import QualityLoop

router = APIRouter(prefix="/v1/reviews", tags=["reviews"])

Status = Literal["open", "approved", "corrected", "rejected"]


class ReviewOut(BaseModel):
    id: int
    answer_id: UUID
    reason: str = Field(examples=["low_confidence"])
    status: Status
    created_at: datetime
    endpoint: str
    question: str = Field(description="Redacted")
    as_of: date
    answer: str
    sources: list[str]
    confidence: float | None
    reviewer: str | None
    corrected_answer: str | None
    note: str | None


class Decision(BaseModel):
    status: Literal["approved", "corrected", "rejected"]
    reviewer: str = Field(min_length=1, max_length=64)
    corrected_answer: str | None = Field(default=None, max_length=8000)
    note: str | None = Field(default=None, max_length=2000)


def _queue(request: Request, authorization: str | None) -> QualityLoop:
    """The queue, for a caller holding the reviewer token; 404 when reviews are not enabled."""
    settings = request.app.state.settings
    token = settings.quality.review_token
    quality: QualityLoop | None = getattr(request.app.state, "quality", None)
    if token is None or quality is None:
        # Not 503: an unconfigured admin surface should look like it does not exist.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    expected = f"Bearer {token.get_secret_value()}"
    if authorization is None or not hmac.compare_digest(authorization, expected):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "reviewer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )  # fmt: skip
    return quality


@router.get("", responses={401: {"description": "Missing or wrong reviewer token"}})
async def list_reviews(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    review_status: Annotated[Status, Query(alias="status")] = "open",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ReviewOut]:
    quality = _queue(request, authorization)
    return [ReviewOut(**r) for r in await quality.reviews(review_status, limit)]


@router.post(
    "/{review_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"description": "Missing or wrong reviewer token"},
        404: {"description": "Unknown review"},
    },
)
async def decide(
    request: Request,
    review_id: Annotated[int, Path(ge=1)],
    body: Decision,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    quality = _queue(request, authorization)
    if body.status == "corrected" and not body.corrected_answer:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "a correction needs corrected_answer"
        )
    found = await quality.resolve(
        review_id,
        status=body.status,
        reviewer=body.reviewer,
        corrected_answer=body.corrected_answer,
        note=body.note,
    )
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown review")
