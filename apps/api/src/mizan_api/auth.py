"""Authentication, rate limiting and the audit log for /v1 (F10).

Every request under ``/v1`` passes through ``AuthMiddleware``:

1. **Who:** with auth on, a bearer JWT (HS256) signed with ``MIZAN_AUTH__JWT_SECRET``, with our
   issuer and audience, unexpired. Its claims give the subject, the tenant and the scopes. The
   ``X-Tenant-Id`` header is ignored, so a caller cannot spend another tenant's budget or read
   its answers. With auth off (local development), the tenant comes from ``X-Tenant-Id`` and
   the caller holds every scope.
2. **How often:** at most ``requests_per_minute`` per subject, in fixed one-minute windows in
   Redis. Excess requests get 429 with ``Retry-After``. This bounds request rate; the daily
   token budget (``budget.py``) bounds cost.
3. **Record:** one ``audit_log`` row per /v1 request: subject, tenant, method, path, status,
   outcome, trace id, latency. Rows are written by a background task, so the database is never
   on the request path; if the queue is full, rows are dropped and counted in the logs.

Scopes: ``ask`` for the endpoints that spend LLM tokens, ``review`` for the review queue.
Search and provision lookups need only a valid token.

    uv run mizan-token --subject alice --tenant acme --scopes ask --days 30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import jwt
from fastapi import HTTPException, Request, status
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mizan_api.budget import tenant_of
from mizan_core.config import AuthSettings
from mizan_core.logging import get_logger
from mizan_core.tracing import current_trace_id

log = get_logger(__name__)

SCOPES = frozenset({"ask", "review"})
ALGORITHM = "HS256"


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    tenant: str
    scopes: frozenset[str] = field(default_factory=frozenset)


class AuthError(Exception):
    def __init__(self, outcome: str, detail: str) -> None:
        super().__init__(detail)
        self.outcome = outcome
        self.detail = detail


def issue_token(
    secret: str,
    *,
    subject: str,
    tenant: str,
    scopes: Collection[str],
    ttl: timedelta,
    issuer: str = "mizan",
    audience: str = "mizan-api",
    now: datetime | None = None,
) -> str:
    unknown = set(scopes) - SCOPES
    if unknown:
        raise ValueError(f"unknown scopes: {sorted(unknown)}")
    if tenant_of(tenant) != tenant:
        raise ValueError("tenant ids are 1-64 letters, digits, '-' or '_'")
    issued = now or datetime.now(UTC)
    claims = {
        "sub": subject,
        "tenant": tenant,
        "scope": " ".join(sorted(scopes)),
        "iss": issuer,
        "aud": audience,
        "iat": issued,
        "exp": issued + ttl,
    }
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


def verify_token(token: str, settings: AuthSettings) -> Principal:
    if settings.jwt_secret is None:  # Settings rejects this when auth is on
        raise AuthError("invalid_token", "authentication is not configured")
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[ALGORITHM],  # never taken from the token header ("alg": "none")
            issuer=settings.issuer,
            audience=settings.audience,
            options={"require": ["sub", "tenant", "exp", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("invalid_token", "token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("invalid_token", "invalid token") from exc
    tenant = str(claims["tenant"])
    if tenant_of(tenant) != tenant:
        raise AuthError("invalid_token", "invalid tenant claim")
    scopes = frozenset(str(claims.get("scope", "")).split()) & SCOPES
    return Principal(subject=str(claims["sub"])[:128], tenant=tenant, scopes=scopes)


class CounterStore(Protocol):
    async def incr(self, key: str) -> int: ...
    async def expire(self, key: str, seconds: int) -> Any: ...


class RateLimiter:
    """Fixed one-minute windows per subject: one INCR per request, the key expires itself."""

    def __init__(
        self, store: CounterStore, per_minute: int, *, clock: Callable[[], float] = time.time
    ) -> None:
        self._store = store
        self.per_minute = per_minute
        self._clock = clock

    async def hit(self, principal: Principal) -> int | None:
        """Seconds to wait when the subject is over its limit, else None."""
        if self.per_minute <= 0:
            return None
        now = self._clock()
        window = int(now // 60)
        key = f"mizan:rate:{principal.tenant}:{principal.subject}:{window}"
        count = await self._store.incr(key)
        if count == 1:
            await self._store.expire(key, 120)
        if count > self.per_minute:
            return max(int(60 - now % 60), 1)
        return None


@dataclass(frozen=True, slots=True)
class AuditRecord:
    subject: str | None
    tenant: str | None
    method: str
    path: str
    status: int
    outcome: str
    trace_id: str | None
    latency_ms: float


AuditWriter = Callable[[list[AuditRecord]], Awaitable[None]]


class AuditLog:
    """A bounded queue drained in batches by one background task."""

    def __init__(self, write: AuditWriter, *, max_queue: int = 10_000, batch: int = 200) -> None:
        self._write = write
        self._queue: asyncio.Queue[AuditRecord] = asyncio.Queue(max_queue)
        self._batch = batch
        self.dropped = 0
        self._task: asyncio.Task[None] | None = None

    def record(self, rec: AuditRecord) -> None:
        if self._task is None:
            self.start()
        log.info("audit", **{k: getattr(rec, k) for k in AuditRecord.__slots__})
        try:
            self._queue.put_nowait(rec)
        except asyncio.QueueFull:
            self.dropped += 1
            log.warning("audit_dropped", dropped=self.dropped)

    def start(self) -> None:
        self._task = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        while True:
            rows = [await self._queue.get()]
            while len(rows) < self._batch and not self._queue.empty():
                rows.append(self._queue.get_nowait())
            try:
                await self._write(rows)
            except Exception:
                log.exception("audit_write_failed", rows=len(rows))

    async def close(self) -> None:
        """Flush what is queued, then stop."""
        if self._task is not None:
            self._task.cancel()
        rows: list[AuditRecord] = []
        while not self._queue.empty():
            rows.append(self._queue.get_nowait())
        if rows:
            await self._write(rows)


def _json(send: Send, code: int, detail: str, headers: dict[str, str]) -> Awaitable[None]:
    async def respond() -> None:
        body = json.dumps({"detail": detail}).encode()
        raw = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
        raw += [(k.lower().encode(), v.encode()) for k, v in headers.items()]
        await send({"type": "http.response.start", "status": code, "headers": raw})
        await send({"type": "http.response.body", "body": body})

    return respond()


class AuthMiddleware:
    """Authenticate, rate-limit and audit every /v1 request (see the module docstring)."""

    def __init__(self, app: ASGIApp, settings: AuthSettings) -> None:
        self.app = app
        self.settings = settings

    def _principal(self, scope: Scope) -> Principal:
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        if not self.settings.enabled:
            tenant = tenant_of(headers.get("x-tenant-id"))
            return Principal(subject="anonymous", tenant=tenant, scopes=SCOPES)
        auth = headers.get("authorization", "")
        kind, _, token = auth.partition(" ")
        if kind.lower() != "bearer" or not token:
            raise AuthError("missing_token", "bearer token required")
        return verify_token(token.strip(), self.settings)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/v1/"):
            await self.app(scope, receive, send)
            return
        state = scope["app"].state
        limiter: RateLimiter | None = getattr(state, "rate_limiter", None)
        audit: AuditLog | None = getattr(state, "audit", None)
        started = time.perf_counter()
        principal: Principal | None = None
        outcome = "ok"
        code = 500

        async def tracked(message: Message) -> None:
            nonlocal code
            if message["type"] == "http.response.start":
                code = message["status"]
            await send(message)

        try:
            principal = self._principal(scope)
            wait = await limiter.hit(principal) if limiter is not None else None
            if wait is not None:
                outcome = "rate_limited"
                await _json(tracked, 429, "rate limit exceeded", {"Retry-After": str(wait)})
                return
            scope.setdefault("state", {})["principal"] = principal
            await self.app(scope, receive, tracked)
            if code == status.HTTP_403_FORBIDDEN:
                outcome = "forbidden"
            # A route may name a more precise outcome (guard.py: "injection").
            outcome = scope["state"].get("audit_outcome", outcome)
        except AuthError as exc:
            outcome = exc.outcome
            await _json(tracked, 401, exc.detail, {"WWW-Authenticate": "Bearer"})
        finally:
            if audit is not None and self.settings.audit:
                audit.record(
                    AuditRecord(
                        subject=principal.subject if principal else None,
                        tenant=principal.tenant if principal else None,
                        method=scope["method"],
                        path=scope["path"][:256],
                        status=code,
                        outcome=outcome,
                        trace_id=current_trace_id(),
                        latency_ms=round((time.perf_counter() - started) * 1000, 1),
                    )
                )


def principal_of(request: Request) -> Principal:
    """The caller, as established by ``AuthMiddleware``.

    Apps built without the middleware (some tests) fall back to the development behaviour.
    """
    found = getattr(request.state, "principal", None)
    if isinstance(found, Principal):
        return found
    return Principal("anonymous", tenant_of(request.headers.get("X-Tenant-Id")), SCOPES)


def tenant_for(request: Request) -> str:
    return principal_of(request).tenant


def require(request: Request, scope: str) -> Principal:
    principal = principal_of(request)
    if scope not in principal.scopes:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"the token lacks the '{scope}' scope")
    return principal


def main(argv: list[str] | None = None) -> None:
    """Issue a token (operators only: it needs the signing secret)."""
    parser = argparse.ArgumentParser(prog="mizan-token")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--scopes", default="ask", help="space- or comma-separated: ask, review")
    parser.add_argument("--days", type=float, default=30)
    args = parser.parse_args(argv)
    from mizan_core.config import get_settings

    settings = get_settings().auth
    secret = settings.jwt_secret
    if secret is None:
        raise SystemExit("set MIZAN_AUTH__JWT_SECRET")
    print(
        issue_token(
            secret.get_secret_value(),
            subject=args.subject,
            tenant=args.tenant,
            scopes=[s for s in args.scopes.replace(",", " ").split() if s],
            ttl=timedelta(days=args.days),
            issuer=settings.issuer,
            audience=settings.audience,
        )
    )
