"""The MCP server against a scripted Mizan API (httpx.MockTransport)."""

import json
import re
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from mizan_mcp.server import MizanApi, build_server

SEARCH = {
    "query": "sınaq",
    "as_of": "2010-01-01",
    "retrieval": "bge-m3",
    "results": [
        {
            "key": "art:51",
            "title": "Maddə 51. Sınaq müddəti",
            "text": "Sınaq müddəti üç aydan çox ola bilməz.",
            "score": 0.8,
            "in_force_since": "1999-07-01",
        }
    ],
}


class Api:
    def __init__(self, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._status = status
        self._headers = headers or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._status != 200:
            return httpx.Response(self._status, json={"detail": "nope"}, headers=self._headers)
        path = request.url.path
        if path == "/v1/search":
            return httpx.Response(200, json=SEARCH)
        if path.startswith("/v1/articles/"):
            return httpx.Response(200, json={"key": path.rsplit("/", 1)[1], "decisions": []})
        if path == "/v1/conflicts":
            return httpx.Response(200, json={"resolutions": [{"rule": "lex specialis"}]})
        if path == "/v1/ask":
            return httpx.Response(
                200,
                json={
                    "answer": "Üç ay.",
                    "refused": False,
                    "as_of": "2026-10-13",
                    "citations": [{"key": "art:51", "title": "...", "quote": "üç aydan"}],
                    "rejected_citations": 1,
                    "sources": [SEARCH["results"][0]],
                    "model": "openai/gpt-oss-120b",
                },
            )
        return httpx.Response(404)


def server_for(api: Api) -> Any:
    client = httpx.AsyncClient(transport=httpx.MockTransport(api), base_url="http://mizan")
    return build_server(MizanApi(client))


def payload(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structured_content", None) or getattr(
        result, "structuredContent", None
    )
    if structured is not None:
        return dict(structured.get("result", structured))
    return dict(json.loads(result.content[0].text))


async def test_the_four_tools_are_listed_read_only() -> None:
    tools = {t.name: t for t in await server_for(Api()).list_tools()}
    assert set(tools) == {"search_law", "get_article", "check_conflicts", "ask_mizan"}
    assert all(t.annotations and t.annotations.read_only_hint for t in tools.values())
    assert "as_of" in json.dumps(tools["search_law"].input_schema)


async def test_search_passes_date_and_k_and_trims_the_result() -> None:
    api = Api()
    result = await server_for(api).call_tool(
        "search_law", {"query": "sınaq müddəti", "as_of": "2010-01-01", "k": 3}
    )
    body = payload(result)
    assert body["results"][0]["key"] == "art:51"
    assert "score" not in body["results"][0]
    params = dict(api.requests[0].url.params)
    assert params == {"q": "sınaq müddəti", "as_of": "2010-01-01", "k": "3"}


async def test_conflicts_send_every_key() -> None:
    api = Api()
    await server_for(api).call_tool("check_conflicts", {"articles": ["art:51", "art:52"]})
    assert api.requests[0].url.params.get_list("keys") == ["art:51", "art:52"]


async def test_get_article_validates_the_key_before_calling_the_api() -> None:
    # The protocol layer turns ToolError into an `isError` result the agent reads.
    api = Api()
    with pytest.raises(ToolError, match="pattern"):
        await server_for(api).call_tool("get_article", {"article": "maddə 5"})
    assert api.requests == []


async def test_ask_returns_citations_and_rejections() -> None:
    api = Api()
    body = payload(await server_for(api).call_tool("ask_mizan", {"question": "Sınaq nə qədərdir?"}))
    assert body["rejected_citations"] == 1
    assert body["sources"] == [{"key": "art:51", "title": "Maddə 51. Sınaq müddəti"}]
    assert json.loads(api.requests[0].content) == {"question": "Sınaq nə qədərdir?"}


@pytest.mark.parametrize(
    ("status", "headers", "message"),
    [
        (429, {"Retry-After": "3600"}, "Mizan API: nope; retry in 3600 s"),
        (503, {}, "Mizan API 503: nope"),
    ],
)
async def test_api_errors_become_tool_errors(
    status: int, headers: dict[str, str], message: str
) -> None:
    with pytest.raises(ToolError, match=re.escape(message)):
        await server_for(Api(status, headers)).call_tool("search_law", {"query": "sınaq"})
