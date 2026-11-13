"""Browser pages: questions at ``/`` and how an article changed at ``/changes``.

The contract page lives with its route in ``contracts.py``.
"""

from __future__ import annotations

from importlib.resources import files

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def ask_page() -> HTMLResponse:
    page = files("mizan_api").joinpath("static/ask.html").read_text(encoding="utf-8")
    return HTMLResponse(page)


@router.get("/changes", response_class=HTMLResponse, include_in_schema=False)
async def changes_page() -> HTMLResponse:
    page = files("mizan_api").joinpath("static/changes.html").read_text(encoding="utf-8")
    return HTMLResponse(page)
