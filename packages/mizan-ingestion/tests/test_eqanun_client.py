from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from mizan_core.config import IngestionSettings
from mizan_ingestion.sources.eqanun import EQanunClient, SourceError

API = "https://api.e-qanun.az/"
SITE = "https://e-qanun.az/"
DOC = b"<html><body><p>Madde 1.</p></body></html>"
SPA_404 = b'<!DOCTYPE html><html><script id="__NEXT_DATA__">{"statusCode":404}</script></html>'


@pytest.fixture
def settings(tmp_path: Path) -> IngestionSettings:
    return IngestionSettings(raw_dir=str(tmp_path), min_request_interval_s=0, max_attempts=3)


@pytest.fixture
async def client(
    settings: IngestionSettings, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[EQanunClient]:
    monkeypatch.setattr(EQanunClient, "_backoff", staticmethod(lambda attempt: 0.0))
    async with EQanunClient(settings) as c:
        yield c


@respx.mock
async def test_framework_metadata(client: EQanunClient) -> None:
    respx.get(f"{API}framework/46943").respond(
        json={
            "data": {
                "id": 46943,
                "requisite": {
                    "title": "Azərbaycan Respublikasının Əmək Məcəlləsi",
                    "acceptDate": "01.02.1999",
                    "typeName": "Məcəllələr",
                    "statusName": "Qüvvədədir",
                    "effectDate": "02.07.1999",
                },
                "htmlUrl": f"{SITE}frameworks/46/f_46943.html",
            }
        }
    )

    meta = await client.get_framework(46943)

    assert meta.title == "Azərbaycan Respublikasının Əmək Məcəlləsi"
    assert meta.accepted_on == date(1999, 2, 1)
    assert meta.html_url.endswith("f_46943.html")


@respx.mock
async def test_missing_framework_is_an_error(client: EQanunClient) -> None:
    respx.get(f"{API}framework/1").respond(json={"data": None})

    with pytest.raises(SourceError, match="not found"):
        await client.get_framework(1)


@respx.mock
async def test_versions_are_sorted_and_resolved(client: EQanunClient) -> None:
    respx.get(f"{API}getVersions?id=46943").respond(
        json={
            "data": [
                {"id": 2, "effectDate": "05.06.2026", "htmlPath": "46/v_46943_13.html"},
                {"id": 1, "effectDate": "01.02.1999", "htmlPath": "46/c_7.html"},
                {"id": 3, "effectDate": None, "htmlPath": "46/broken.html"},
            ]
        }
    )

    versions = await client.get_versions(46943)

    assert [v.source_version_id for v in versions] == [1, 2]
    assert versions[1].html_url == f"{SITE}versions/46/v_46943_13.html"


@respx.mock
async def test_fetch_sends_honest_user_agent_and_referer(client: EQanunClient) -> None:
    route = respx.get(f"{SITE}versions/46/v_1.html").respond(content=DOC)

    await client.fetch_html(f"{SITE}versions/46/v_1.html", immutable=True)

    request = route.calls.last.request
    assert request.headers["Referer"] == SITE
    assert request.headers["User-Agent"].startswith("Mizan-research/")


@respx.mock
async def test_immutable_documents_are_downloaded_once(
    client: EQanunClient, settings: IngestionSettings
) -> None:
    route = respx.get(f"{SITE}versions/46/v_1.html").respond(content=DOC)

    first = await client.fetch_html(f"{SITE}versions/46/v_1.html", immutable=True)
    second = await client.fetch_html(f"{SITE}versions/46/v_1.html", immutable=True)

    assert route.call_count == 1
    assert (first.from_cache, second.from_cache) == (False, True)
    assert first.sha256 == second.sha256
    assert (Path(settings.raw_dir) / "e-qanun/versions/46/v_1.html").read_bytes() == DOC


@respx.mock
async def test_mutable_documents_are_always_refetched(client: EQanunClient) -> None:
    route = respx.get(f"{SITE}frameworks/46/f_1.html").respond(content=DOC)

    await client.fetch_html(f"{SITE}frameworks/46/f_1.html", immutable=False)
    await client.fetch_html(f"{SITE}frameworks/46/f_1.html", immutable=False)

    assert route.call_count == 2


@respx.mock
async def test_retries_transient_errors(client: EQanunClient) -> None:
    route = respx.get(f"{SITE}versions/46/v_1.html")
    route.side_effect = [
        httpx.Response(503),
        httpx.ConnectError("reset"),
        httpx.Response(200, content=DOC),
    ]

    raw = await client.fetch_html(f"{SITE}versions/46/v_1.html", immutable=True)

    assert raw.content == DOC
    assert route.call_count == 3


@respx.mock
async def test_gives_up_after_max_attempts(client: EQanunClient) -> None:
    respx.get(f"{SITE}versions/46/v_1.html").respond(503)

    with pytest.raises(SourceError, match="after retries"):
        await client.fetch_html(f"{SITE}versions/46/v_1.html", immutable=True)


@respx.mock
async def test_client_errors_are_not_retried(client: EQanunClient) -> None:
    route = respx.get(f"{SITE}versions/46/v_1.html").respond(403)

    with pytest.raises(SourceError, match="403"):
        await client.fetch_html(f"{SITE}versions/46/v_1.html", immutable=True)
    assert route.call_count == 1


@respx.mock
async def test_spa_not_found_page_is_not_cached_as_a_document(
    client: EQanunClient, settings: IngestionSettings
) -> None:
    respx.get(f"{SITE}versions/46/missing.html").respond(content=SPA_404)

    with pytest.raises(SourceError, match="did not return a document"):
        await client.fetch_html(f"{SITE}versions/46/missing.html", immutable=True)
    assert not list(Path(settings.raw_dir).rglob("*.html"))
