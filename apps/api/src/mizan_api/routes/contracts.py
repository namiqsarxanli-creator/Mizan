"""Employment-contract analysis (F10): upload a contract, get a clause-by-clause report.

``POST /v1/contracts/analyze`` takes a PDF, DOCX, text file or photo (multipart ``file``).
The pipeline is ``mizan_contracts.analysis``: rules always, the LLM review and OCR when a Groq
key is configured. LLM tokens count against the tenant's daily budget, like ``/v1/ask``.

The contract is untrusted input. A clause carrying instructions to the model is reported as
``injection`` and never sent to it; the request is audited with outcome ``injection``. Unlike
a question, the upload is not refused: the person uploading is usually not the attacker but
the one the hidden text is aimed at, and the report should tell them.

``GET /contracts`` serves a one-page UI for the same endpoint.
"""

from __future__ import annotations

from datetime import date
from importlib.resources import files
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from mizan_api.auth import require
from mizan_api.budget import TenantBudget
from mizan_api.routes.legal import today_in_baku
from mizan_contracts.analysis import DISCLAIMER, Report, analyse
from mizan_contracts.document import MAX_BYTES, DocumentError, Ocr, read_document
from mizan_contracts.review import ClauseReviewer
from mizan_core.logging import get_logger

log = get_logger(__name__)
router = APIRouter(tags=["contracts"])


class FindingOut(BaseModel):
    verdict: str = Field(examples=["violation"])
    issue: str = Field(examples=["probation_too_long"])
    articles: list[str] = Field(examples=[["art:51"]])
    explanation: str
    origin: str = Field(description="rule, llm or screen", examples=["rule"])
    quote: str | None = Field(default=None, description="the statute's words, verified")


class ClauseOut(BaseModel):
    number: str = Field(examples=["2.3"])
    section: str | None
    text: str
    verdict: str = Field(examples=["violation"])
    findings: list[FindingOut]
    model_form: str | None = Field(
        default=None, description="the clause of the Code's model form (annex 1) it reproduces"
    )


class ContractReport(BaseModel):
    filename: str
    as_of: date
    counts: dict[str, int]
    clauses: list[ClauseOut]
    llm_reviewed: bool
    review_failed_batches: int
    ocr_pages: list[int]
    unreadable_pages: list[int]
    tokens: int
    disclaimer: str = DISCLAIMER


def _out(report: Report) -> ContractReport:
    return ContractReport(
        filename=report.filename,
        as_of=report.as_of,
        counts=report.counts,
        clauses=[
            ClauseOut(
                number=c.number,
                section=c.section,
                text=c.text,
                verdict=c.verdict,
                findings=[
                    FindingOut(
                        verdict=f.verdict,
                        issue=f.issue,
                        articles=list(f.articles),
                        explanation=f.explanation,
                        origin=f.origin,
                        quote=f.quote,
                    )
                    for f in c.findings
                ],
                model_form=c.model_form,
            )
            for c in report.clauses
        ],
        llm_reviewed=report.llm_reviewed,
        review_failed_batches=report.review.failed,
        ocr_pages=list(report.ocr_pages),
        unreadable_pages=list(report.unreadable_pages),
        tokens=report.review.input_tokens + report.review.output_tokens,
        disclaimer=report.disclaimer,
    )


@router.post(
    "/v1/contracts/analyze",
    responses={413: {"description": "file too large"}, 422: {"description": "unreadable"}},
)
async def analyze(
    request: Request,
    file: Annotated[UploadFile, File(description=".pdf, .docx, .txt, .png or .jpg")],
    as_of: Annotated[date | None, Form()] = None,
    llm: Annotated[bool, Form(description="false: rules only, no tokens spent")] = True,
) -> ContractReport:
    tenant = require(request, "ask").tenant
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "at most 10 MB")
    state = request.app.state
    reviewer: ClauseReviewer | None = getattr(state, "contract_reviewer", None) if llm else None
    ocr: Ocr | None = getattr(state, "ocr", None)
    budget: TenantBudget | None = getattr(state, "budget", None)
    if budget is not None and (reviewer is not None or ocr is not None):
        await budget.check(tenant)
    try:
        document = await read_document(data, file.filename or "contract", ocr=ocr)
    except DocumentError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if not document.text.strip():
        detail = "no text found" + ("" if ocr else "; scanned documents need OCR (a Groq key)")
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail)
    report = await analyse(document, as_of=as_of or today_in_baku(), reviewer=reviewer)
    out = _out(report)
    if budget is not None and out.tokens:
        await budget.charge(tenant, out.tokens)
    if report.counts["injection"]:
        request.state.audit_outcome = "injection"
    log.info(
        "contract_analysed",
        clauses=len(report.clauses),
        counts=report.counts,
        ocr_pages=len(report.ocr_pages),
        tokens=out.tokens,
    )
    return out


@router.get("/contracts", response_class=HTMLResponse, include_in_schema=False)
async def contracts_page() -> HTMLResponse:
    page = files("mizan_api").joinpath("static/contracts.html").read_text(encoding="utf-8")
    return HTMLResponse(page)
