"""End-to-end sync against a real Postgres, with e-qanun mocked at the HTTP layer.

docker compose up -d postgres
MIZAN_INTEGRATION=1 uv run pytest -m integration
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import respx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from word_fixtures import article, centered, document, encode, para

from mizan_core.config import IngestionSettings
from mizan_ingestion import pipeline
from mizan_ingestion.db import repository as repo
from mizan_ingestion.db.schema import DocumentVersion, Provision, ProvisionText
from mizan_ingestion.parsing import PARSER_VERSION
from mizan_ingestion.pipeline import Outcome, sync_document
from mizan_ingestion.sources.eqanun import SOURCE, EQanunClient

pytestmark = pytest.mark.integration

API = "https://api.e-qanun.az/"
SITE = "https://e-qanun.az/"
DOC_ID = 46943


def _code(part1: str, *, extra_article: bool = False) -> bytes:
    blocks = [
        centered("ƏMƏK MƏCƏLLƏSİ"),
        centered("I bölmə"),
        centered("Ümumi normalar"),
        article("1", "Sabit maddə"),
        para("Bu maddə heç vaxt dəyişmir."),
        article("69", "Xitam", edn=1),
        para(f"1. {part1}"),
        para("2. İkinci hissə."),
    ]
    notes = {1: "15 dekabr 2022-ci il tarixli 100-VIQD nömrəli Qanun ilə dəyişdirilmişdir."}
    if extra_article:
        blocks += [article("70", "Yeni maddə", edn=2), para("Yeni mətn.")]
        notes[2] = "1 mart 2024-cü il tarixli 200-VIIQD nömrəli Qanun ilə əlavə edilmişdir."
    return encode(document(*blocks, endnotes=notes))


V2020 = _code("Köhnə redaksiya.")
V2023 = _code("Yeni redaksiya.")
CURRENT = _code("Yeni redaksiya.", extra_article=True)


class FakeEQanun:
    """Mutable fake of e-qanun's API + static files."""

    def __init__(self) -> None:
        self.versions: list[tuple[int, str, bytes]] = [
            (1, "01.01.2020", V2020),
            (2, "01.01.2023", V2023),
        ]
        self.current = CURRENT
        self.router = respx.mock(assert_all_called=False)

    def install(self) -> respx.MockRouter:
        r = self.router
        r.get(f"{API}framework/{DOC_ID}").mock(side_effect=lambda _: self._framework())
        r.get(f"{API}getVersions?id={DOC_ID}").mock(side_effect=lambda _: self._versions())
        r.get(url__regex=rf"{SITE}versions/46/v_(\d+)\.html").mock(side_effect=self._version_html)
        r.get(f"{SITE}frameworks/46/f_{DOC_ID}.html").mock(
            side_effect=lambda _: respx.MockResponse(content=self.current)
        )
        return r

    def _framework(self) -> Any:
        return respx.MockResponse(
            json={
                "data": {
                    "requisite": {"title": "Əmək Məcəlləsi", "acceptDate": "01.02.1999"},
                    "htmlUrl": f"{SITE}frameworks/46/f_{DOC_ID}.html",
                }
            }
        )

    def _versions(self) -> Any:
        return respx.MockResponse(
            json={
                "data": [
                    {"id": vid, "effectDate": eff, "htmlPath": f"46/v_{vid}.html"}
                    for vid, eff, _ in self.versions
                ]
            }
        )

    def _version_html(self, request: Any) -> Any:
        vid = int(str(request.url).rsplit("_", 1)[1].removesuffix(".html"))
        body = next(b for v, _, b in self.versions if v == vid)
        return respx.MockResponse(content=body)

    def calls_to(self, fragment: str) -> int:
        return sum(fragment in str(c.request.url) for c in self.router.calls)


@pytest.fixture
def source() -> Iterator[FakeEQanun]:
    fake = FakeEQanun()
    with fake.install():
        yield fake


@pytest.fixture
def ingestion_settings(tmp_path: Path) -> IngestionSettings:
    return IngestionSettings(raw_dir=str(tmp_path), min_request_interval_s=0)


async def _sync(
    settings: IngestionSettings, sessions: async_sessionmaker[AsyncSession]
) -> pipeline.SyncReport:
    async with EQanunClient(settings) as client:
        return await sync_document(client, sessions, DOC_ID)


async def _versions(sessions: async_sessionmaker[AsyncSession]) -> list[DocumentVersion]:
    async with sessions() as s:
        rows = await s.execute(select(DocumentVersion).order_by(DocumentVersion.valid_from))
        return list(rows.scalars())


async def _part1_on(sessions: async_sessionmaker[AsyncSession], on: date) -> str | None:
    async with sessions() as s:
        _, provisions = await repo.provisions_as_of(
            s, source=SOURCE, source_id=DOC_ID, on=on, key_prefix="art:69/part:1"
        )
    return provisions[0].content.text if provisions else None


async def test_first_sync_builds_a_temporal_history(
    source: FakeEQanun,
    ingestion_settings: IngestionSettings,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    report = await _sync(ingestion_settings, sessions)

    assert report.outcomes == {Outcome.INSERTED: 3}
    versions = await _versions(sessions)
    assert [(v.valid_from, v.valid_to) for v in versions] == [
        (date(2020, 1, 1), date(2023, 1, 1)),
        (date(2023, 1, 1), date(2024, 3, 1)),
        # Undated current text: starts on the adoption date of its newest amending act.
        (date(2024, 3, 1), None),
    ]
    assert [v.valid_from_estimated for v in versions] == [False, False, True]
    assert [v.is_current for v in versions] == [False, False, True]


async def test_as_of_queries_return_the_text_in_force(
    source: FakeEQanun,
    ingestion_settings: IngestionSettings,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _sync(ingestion_settings, sessions)

    assert await _part1_on(sessions, date(2019, 12, 31)) is None
    assert await _part1_on(sessions, date(2021, 6, 1)) == "Köhnə redaksiya."
    assert await _part1_on(sessions, date(2022, 12, 31)) == "Köhnə redaksiya."
    assert await _part1_on(sessions, date(2023, 1, 1)) == "Yeni redaksiya."
    assert await _part1_on(sessions, date(2030, 1, 1)) == "Yeni redaksiya."


async def test_unchanged_text_is_stored_once(
    source: FakeEQanun,
    ingestion_settings: IngestionSettings,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _sync(ingestion_settings, sessions)

    async with sessions() as s:
        provisions = await s.scalar(select(func.count()).select_from(Provision))
        texts = await s.scalar(select(func.count()).select_from(ProvisionText))
        art1_hashes = await s.scalar(
            select(func.count(func.distinct(Provision.content_hash))).where(
                Provision.key == "art:1"
            )
        )
    assert art1_hashes == 1
    assert texts is not None
    assert provisions is not None
    assert texts < provisions


async def test_second_sync_is_a_no_op(
    source: FakeEQanun,
    ingestion_settings: IngestionSettings,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _sync(ingestion_settings, sessions)
    before = [(v.id, v.valid_from, v.valid_to) for v in await _versions(sessions)]

    report = await _sync(ingestion_settings, sessions)

    assert report.outcomes == {Outcome.UNCHANGED: 3}
    assert [(v.id, v.valid_from, v.valid_to) for v in await _versions(sessions)] == before
    # Historical versions come from the disk cache; only the current text is re-downloaded.
    assert source.calls_to("/versions/46/") == 2
    assert source.calls_to(f"f_{DOC_ID}") == 2


async def test_parser_upgrade_reparses_stored_versions(
    source: FakeEQanun,
    ingestion_settings: IngestionSettings,
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _sync(ingestion_settings, sessions)
    monkeypatch.setattr(pipeline, "PARSER_VERSION", PARSER_VERSION + 1)

    report = await _sync(ingestion_settings, sessions)

    assert report.outcomes == {Outcome.REPARSED: 3}
    assert len(await _versions(sessions)) == 3
    assert await _part1_on(sessions, date(2021, 6, 1)) == "Köhnə redaksiya."


async def test_current_text_gets_its_real_date_once_published_as_a_version(
    source: FakeEQanun,
    ingestion_settings: IngestionSettings,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _sync(ingestion_settings, sessions)
    first_current = next(v for v in await _versions(sessions) if v.is_current)

    # e-qanun archives the old current text as a dated version and publishes a new one.
    source.versions.append((3, "20.03.2024", CURRENT))
    source.current = _code("Ən yeni redaksiya.", extra_article=True)
    report = await _sync(ingestion_settings, sessions)

    assert report.outcomes == {Outcome.UNCHANGED: 3, Outcome.INSERTED: 1}
    versions = await _versions(sessions)
    archived = next(v for v in versions if v.id == first_current.id)
    assert (archived.valid_from, archived.valid_from_estimated) == (date(2024, 3, 20), False)
    assert not archived.is_current
    assert versions[-1].is_current
    assert versions[-1].valid_from > date(2024, 3, 20)
    assert await _part1_on(sessions, date(2030, 1, 1)) == "Ən yeni redaksiya."
