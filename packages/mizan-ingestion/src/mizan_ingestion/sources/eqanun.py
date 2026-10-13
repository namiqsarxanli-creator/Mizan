"""Client for e-qanun.az, the official legal database of Azerbaijan (Ministry of Justice).

Being a good citizen of a public-service site is a requirement, not an optimisation:

* one request at a time, at most one every ``min_request_interval_s`` seconds;
* an honest User-Agent that links back to this project;
* historical versions are immutable, so each is downloaded once and served from disk after;
* retries with exponential backoff and jitter on 429/5xx, honouring ``Retry-After``.

The site's static HTML is guarded against hotlinking: it is served only with a same-site
``Referer``, which is what its own frontend sends.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Self
from urllib.parse import urljoin, urlparse

import httpx

from mizan_core.config import IngestionSettings
from mizan_core.logging import get_logger

log = get_logger(__name__)

SOURCE = "e-qanun"
_RETRYABLE = {429, 500, 502, 503, 504}


@dataclass(frozen=True, slots=True)
class FrameworkMeta:
    source_id: int
    title: str
    doc_type: str | None
    status: str | None
    accepted_on: date | None
    effective_on: date | None
    html_url: str


@dataclass(frozen=True, slots=True)
class VersionMeta:
    source_version_id: int
    effective_on: date
    html_url: str


@dataclass(frozen=True, slots=True)
class RawDocument:
    url: str
    content: bytes
    from_cache: bool

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


class SourceError(RuntimeError):
    pass


def _write_atomically(path: Path, content: bytes) -> None:
    """Write via a temp file and rename, so a crash never leaves a truncated cache entry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(content)
    tmp.replace(path)


def _parse_date(value: str | None) -> date | None:
    return datetime.strptime(value, "%d.%m.%Y").date() if value else None  # noqa: DTZ007


class EQanunClient:
    def __init__(self, settings: IngestionSettings, http: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._http = http or httpx.AsyncClient(
            timeout=settings.request_timeout_s,
            follow_redirects=True,
            headers={"User-Agent": settings.user_agent},
        )
        self._owns_http = http is None
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._cache_dir = Path(settings.raw_dir) / SOURCE

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_http:
            await self._http.aclose()

    # ------------------------------------------------------------------ API

    async def get_framework(self, source_id: int) -> FrameworkMeta:
        payload = await self._get_json(f"framework/{source_id}")
        data = payload.get("data") or {}
        req = data.get("requisite") or {}
        if not data.get("htmlUrl") or not req.get("title"):
            raise SourceError(f"framework {source_id} not found or incomplete")
        return FrameworkMeta(
            source_id=source_id,
            title=req["title"],
            doc_type=req.get("typeName"),
            status=req.get("statusName"),
            accepted_on=_parse_date(req.get("acceptDate")),
            effective_on=_parse_date(req.get("effectDate")),
            html_url=data["htmlUrl"],
        )

    async def get_versions(self, source_id: int) -> list[VersionMeta]:
        payload = await self._get_json(f"getVersions?id={source_id}")
        versions = [
            VersionMeta(
                source_version_id=int(v["id"]),
                effective_on=d,
                html_url=urljoin(self._settings.eqanun_site_url, f"versions/{v['htmlPath']}"),
            )
            for v in payload.get("data") or []
            if v.get("htmlPath") and (d := _parse_date(v.get("effectDate")))
        ]
        return sorted(versions, key=lambda v: v.effective_on)

    async def fetch_html(self, url: str, *, immutable: bool) -> RawDocument:
        """Download a document; ``immutable`` documents are cached on disk forever."""
        path = self._cache_dir / urlparse(url).path.lstrip("/")
        if immutable and await asyncio.to_thread(path.exists):
            return RawDocument(url, await asyncio.to_thread(path.read_bytes), from_cache=True)

        response = await self._request(url, headers={"Referer": self._settings.eqanun_site_url})
        content = response.content
        if b"<html" not in content[:2048].lower() or b"__NEXT_DATA__" in content:
            # The site answers unknown paths with its SPA 404 page and status 200 or 404.
            raise SourceError(f"{url} did not return a document")
        # Off the event loop: documents are 5-10 MB.
        await asyncio.to_thread(_write_atomically, path, content)
        return RawDocument(url, content, from_cache=False)

    # ------------------------------------------------------------ transport

    async def _get_json(self, path: str) -> dict[str, Any]:
        url = urljoin(self._settings.eqanun_api_url, path)
        response = await self._request(url, headers={"Accept-Language": "AZ"})
        body: dict[str, Any] = response.json()
        return body

    async def _request(self, url: str, *, headers: dict[str, str]) -> httpx.Response:
        attempts = self._settings.max_attempts
        for attempt in range(1, attempts + 1):
            try:
                # Serialised and spaced out: at most one in-flight request per client.
                async with self._lock:
                    await self._wait_turn()
                    response = await self._http.get(url, headers=headers)
            except httpx.TransportError as exc:
                if attempt == attempts:
                    raise SourceError(f"GET {url} failed: {exc!r}") from exc
                delay = self._backoff(attempt)
                log.warning(
                    "source_retry", url=url, attempt=attempt, error=repr(exc), delay_s=delay
                )
            else:
                if response.status_code not in _RETRYABLE:
                    if response.is_error:
                        raise SourceError(f"GET {url} -> HTTP {response.status_code}")
                    return response
                if attempt == attempts:
                    raise SourceError(f"GET {url} -> HTTP {response.status_code} after retries")
                delay = self._retry_after(response) or self._backoff(attempt)
                log.warning(
                    "source_retry", url=url, attempt=attempt, status=response.status_code,
                    delay_s=delay,
                )  # fmt: skip
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    async def _wait_turn(self) -> None:
        wait = self._last_request + self._settings.min_request_interval_s - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request = time.monotonic()

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(30.0, 2.0**attempt) * random.uniform(0.5, 1.0)  # noqa: S311

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        value = response.headers.get("Retry-After", "")
        return min(float(value), 120.0) if value.isdigit() else None
