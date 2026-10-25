"""User feedback on an answer: the start of the data flywheel (F6)."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from mizan_api.quality import QualityLoop

router = APIRouter(prefix="/v1", tags=["feedback"])


class FeedbackRequest(BaseModel):
    answer_id: UUID
    rating: Literal[1, -1] = Field(description="1 = 👍, -1 = 👎")
    comment: str | None = Field(default=None, max_length=1000)


@router.post(
    "/feedback",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"description": "Unknown answer id"}, 503: {"description": "Disabled"}},
)
async def feedback(request: Request, body: FeedbackRequest) -> None:
    quality: QualityLoop | None = getattr(request.app.state, "quality", None)
    if quality is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "feedback is disabled")
    if not await quality.feedback(body.answer_id, body.rating, body.comment):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown answer id")
