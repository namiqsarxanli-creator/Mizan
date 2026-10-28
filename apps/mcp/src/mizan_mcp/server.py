"""Mizan as an MCP server: Azerbaijani labour law tools for Claude Desktop and other agents.

The server is a thin client of the Mizan HTTP API rather than a second copy of the stack. It
holds no models, databases or LLM keys, and every call goes through the API's tenant budget, rate
limits, tracing and quality loop. Its job is translation: tool schemas an agent can read, API
errors turned into messages an agent can act on.

Configuration (environment):

- ``MIZAN_API_URL``: the API's base URL, default ``http://127.0.0.1:18000``.
- ``MIZAN_TENANT_ID``: sent as ``X-Tenant-Id``, so the API's per-tenant token budget applies.

Run it over stdio (Claude Desktop) with ``mizan-mcp``.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Annotated, Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

INSTRUCTIONS = """\
Tools over the Labour Code of the Republic of Azerbaijan (Əmək Məcəlləsi), with every \
provision's text as in force on a chosen date, the citation graph between articles, and the \
Constitutional Court decisions that interpret them.

- Article keys look like "art:69" (article 69) or "art:118-1" (an inserted article).
- `as_of` is an ISO date (YYYY-MM-DD). Without it, today's law (Baku time) is used. Ask about a \
past date to get the text that was in force then.
- Prefer `search_law` and `get_article` when you will reason over the text yourself. Use \
`ask_mizan` for a sourced answer in Azerbaijani; it costs the tenant's LLM budget.
- Quote the law from tool results only. If a tool says nothing applies, say so.\
"""

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)

AsOf = Annotated[
    date | None,
    Field(description="Date the law is read on (YYYY-MM-DD); default today in Baku"),
]
ArticleKey = Annotated[
    str, Field(pattern=r"^(art|annex):[0-9]+(-[0-9]+)?$", examples=["art:69", "art:118-1"])
]


class MizanApi:
    """The HTTP API, with its errors mapped to messages an agent can act on."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    @classmethod
    def from_env(cls) -> MizanApi:
        headers = {"User-Agent": "mizan-mcp"}
        if tenant := os.environ.get("MIZAN_TENANT_ID"):
            headers["X-Tenant-Id"] = tenant
        return cls(
            httpx.AsyncClient(
                base_url=os.environ.get("MIZAN_API_URL", "http://127.0.0.1:18000"),
                headers=headers,
                # ask_mizan waits for an LLM; the others answer in well under a second.
                timeout=httpx.Timeout(90.0, connect=5.0),
            )
        )

    async def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise ToolError(
                f"Mizan API is unreachable ({type(exc).__name__}); is it running?"
            ) from exc
        if response.status_code == 429:
            retry = response.headers.get("Retry-After")
            raise ToolError(
                "The daily token budget for this tenant is spent"
                + (f"; retry in {retry} s." if retry else ".")
            )
        if response.status_code >= 400:
            detail = _detail(response)
            raise ToolError(f"Mizan API {response.status_code}: {detail}")
        body: dict[str, Any] = response.json()
        return body

    async def aclose(self) -> None:
        await self._client.aclose()


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail", response.text)
    except ValueError:
        detail = response.text
    return str(detail)[:300]


def _params(**values: Any) -> dict[str, Any]:
    return {
        k: v.isoformat() if isinstance(v, date) else v for k, v in values.items() if v is not None
    }


def build_server(api: MizanApi) -> MCPServer:
    server = MCPServer(
        name="mizan", title="Mizan — Azerbaijani labour law", instructions=INSTRUCTIONS
    )

    @server.tool(annotations=READ_ONLY)
    async def search_law(
        query: Annotated[
            str, Field(min_length=2, max_length=500, description="Question or keywords")
        ],
        as_of: AsOf = None,
        k: Annotated[int, Field(ge=1, le=20)] = 5,
    ) -> dict[str, Any]:
        """Search the Labour Code: the provisions most relevant to a question, on a date.

        Results carry the provision key, its title, its text and the date that text took effect.
        """
        body = await api.request("GET", "/v1/search", params=_params(q=query, as_of=as_of, k=k))
        return {
            "as_of": body["as_of"],
            "results": [
                {
                    "key": r["key"],
                    "title": r["title"],
                    "text": r["text"],
                    "in_force_since": r["in_force_since"],
                }
                for r in body["results"]
            ],
        }

    @server.tool(annotations=READ_ONLY)
    async def get_article(article: ArticleKey, as_of: AsOf = None) -> dict[str, Any]:
        """One whole article as in force on a date, with the Constitutional Court decisions
        that interpret it (a decision can override the article's literal text)."""
        return await api.request("GET", f"/v1/articles/{article}", params=_params(as_of=as_of))

    @server.tool(annotations=READ_ONLY)
    async def check_conflicts(
        articles: Annotated[list[ArticleKey], Field(min_length=1, max_length=20)],
        as_of: AsOf = None,
    ) -> dict[str, Any]:
        """Which of several articles prevails, by the collision rules of the Normative Legal Acts
        law: the Code's own exceptions (lex specialis), and provisions the Constitutional Court
        suspended or voided. An empty list means no collision the citation graph can see."""
        params = [("keys", key) for key in articles] + list(_params(as_of=as_of).items())
        return await api.request("GET", "/v1/conflicts", params=params)

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
    async def ask_mizan(
        question: Annotated[str, Field(min_length=3, max_length=1000)],
        as_of: AsOf = None,
    ) -> dict[str, Any]:
        """A sourced answer in Azerbaijani from Mizan's question-answering pipeline.

        Every citation is a verbatim quote checked against its source; quotes that did not match
        were dropped (`rejected_citations`). `refused` is true when the sources do not answer the
        question. Costs the tenant's daily LLM budget.
        """
        payload: dict[str, Any] = {"question": question}
        if as_of is not None:
            payload["as_of"] = as_of.isoformat()
        body = await api.request("POST", "/v1/ask", json=payload)
        return {
            "answer": body["answer"],
            "refused": body["refused"],
            "as_of": body["as_of"],
            "citations": body["citations"],
            "rejected_citations": body.get("rejected_citations", 0),
            "sources": [{"key": s["key"], "title": s["title"]} for s in body["sources"]],
        }

    return server


def main() -> None:
    build_server(MizanApi.from_env()).run("stdio")


if __name__ == "__main__":
    main()
