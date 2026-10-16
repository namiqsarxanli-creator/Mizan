"""Query rewriting: everyday Azerbaijani -> the Code's terminology.

People ask "məni işdən qova bilərlərmi?"; the Code says "əmək müqaviləsinə işəgötürənin
təşəbbüsü ilə xitam verilməsi". A small, fast model restates the question in legal terms, and
the search runs on *both* (query expansion, not replacement): if the rewrite drifts, the
original wording still carries its own signal.

Any failure (timeout, rate limit, empty output) falls back to the original question; rewriting
may improve retrieval but must never break it.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from mizan_core.logging import get_logger

log = get_logger(__name__)

DEFAULT_REWRITE_MODEL = "openai/gpt-oss-20b"

REWRITE_PROMPT = """\
Sən Azərbaycan əmək hüququ üzrə axtarış köməkçisisən. İstifadəçinin sualını Azərbaycan \
Respublikasının Əmək Məcəlləsində işlənən hüquqi terminlərlə bir cümlədə yenidən ifadə et.
- Gündəlik sözləri hüquqi terminlərə çevir (məs. "işdən qovmaq" → "əmək müqaviləsinə \
işəgötürənin təşəbbüsü ilə xitam verilməsi", "maaş" → "əmək haqqı").
- Maddə nömrəsi uydurma, suala cavab vermə, izah yazma.
- Yalnız yenidən ifadə olunmuş sualı qaytar."""


class QueryRewriter(Protocol):
    async def expand(self, question: str) -> str: ...


class GroqQueryRewriter:
    def __init__(
        self, client: Any, *, model: str = DEFAULT_REWRITE_MODEL, timeout_s: float = 5.0
    ) -> None:
        """``client`` is a ``groq.AsyncGroq`` (typed loosely so tests can fake it)."""
        self._client = client
        self._model = model
        self._timeout_s = timeout_s

    def _extra(self) -> dict[str, Any]:
        # A one-sentence restatement needs little reasoning; keep it fast.
        return {"reasoning_effort": "low"} if "gpt-oss" in self._model else {}

    async def rewrite(self, question: str) -> str | None:
        try:
            async with asyncio.timeout(self._timeout_s):
                response = await self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": REWRITE_PROMPT},
                        {"role": "user", "content": question},
                    ],
                    temperature=0.0,
                    max_completion_tokens=1024,  # reasoning tokens count against this
                    **self._extra(),
                )
        except Exception as exc:  # noqa: BLE001 - degrade to the original query
            log.warning("query_rewrite_failed", error=repr(exc))
            return None
        text = (response.choices[0].message.content or "").strip().strip('"')
        return text or None

    async def expand(self, question: str) -> str:
        rewritten = await self.rewrite(question)
        if not rewritten or rewritten.casefold() == question.casefold():
            return question
        return f"{question}\n{rewritten}"
