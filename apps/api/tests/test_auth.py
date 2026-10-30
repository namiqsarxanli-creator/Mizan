"""Authentication, scopes, rate limiting and the audit log (F10)."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from fakes import fake_legal_services
from fastapi.testclient import TestClient
from pydantic import SecretStr

from mizan_api.auth import (
    AuditLog,
    AuditRecord,
    AuthError,
    Principal,
    RateLimiter,
    issue_token,
    verify_token,
)
from mizan_api.main import create_app
from mizan_core.config import AuthSettings, Environment, Settings

SECRET = "s" * 40
AUTH = AuthSettings(enabled=True, jwt_secret=SecretStr(SECRET))


def token(**overrides: Any) -> str:
    kwargs: dict[str, Any] = {
        "subject": "alice",
        "tenant": "acme",
        "scopes": ["ask"],
        "ttl": timedelta(hours=1),
    }
    return issue_token(SECRET, **(kwargs | overrides))


class Counters:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key: str, seconds: int) -> None:
        return None


class Budget:
    """Records which tenant a request was charged to."""

    def __init__(self) -> None:
        self.checked: list[str] = []

    async def check(self, tenant: str) -> None:
        self.checked.append(tenant)

    async def charge(self, tenant: str, tokens: int) -> int:
        return tokens


def client(
    auth: AuthSettings = AUTH, *, per_minute: int = 0, audit: list[AuditRecord] | None = None
) -> tuple[TestClient, Budget]:
    settings = Settings(_env_file=None, environment=Environment.TEST, auth=auth)
    budget = Budget()

    async def write(rows: list[AuditRecord]) -> None:
        if audit is not None:
            audit.extend(rows)

    log = AuditLog(write)  # its drain task starts with the first record
    services = replace(
        fake_legal_services(with_llm=True),
        budget=budget,  # type: ignore[arg-type]
        rate_limiter=RateLimiter(Counters(), per_minute),
        audit=log,
    )
    return TestClient(create_app(settings, checks=[], legal=services)), budget


def raw(claims: dict[str, Any], algorithm: str = "HS256") -> str:
    """A token built by hand, as an attacker would."""
    base = {
        "sub": "a",
        "iss": "mizan",
        "aud": "mizan-api",
        "exp": datetime.now(UTC) + timedelta(hours=1),
    }
    full = {k: v for k, v in (base | claims).items() if v is not None}
    return jwt.encode(full, SECRET if algorithm != "none" else "", algorithm=algorithm)


def bearer(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


ASK = {"question": "Sınaq müddəti nə qədərdir?"}


# ---------------------------------------------------------------------------- tokens


def test_a_token_round_trips() -> None:
    p = verify_token(token(scopes=["ask", "review"]), AUTH)
    assert p == Principal("alice", "acme", frozenset({"ask", "review"}))


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(lambda: token(ttl=timedelta(seconds=-1)), id="expired"),
        pytest.param(
            lambda: issue_token("x" * 40, subject="a", tenant="t", scopes=[], ttl=timedelta(1)),
            id="other-key",
        ),
        pytest.param(lambda: token(audience="someone-else"), id="audience"),
        pytest.param(lambda: token(issuer="someone-else"), id="issuer"),
        pytest.param(lambda: raw({"tenant": "t"}, algorithm="none"), id="alg-none"),
        pytest.param(lambda: raw({"tenant": "../x"}), id="tenant-injection"),
        pytest.param(lambda: raw({"tenant": "t", "exp": None}), id="no-expiry"),
        pytest.param(lambda: "not.a.jwt", id="garbage"),
    ],
)
def test_invalid_tokens_are_rejected(bad: Any) -> None:
    with pytest.raises(AuthError):
        verify_token(bad(), AUTH)


def test_unknown_scopes_cannot_be_issued_and_are_ignored_when_present() -> None:
    with pytest.raises(ValueError, match="unknown scopes"):
        token(scopes=["admin"])
    forged = raw({"tenant": "t", "scope": "ask admin"})
    assert verify_token(forged, AUTH).scopes == frozenset({"ask"})


# ---------------------------------------------------------------------------- the API


def test_v1_needs_a_token_but_health_does_not() -> None:
    c, _ = client()
    with c:
        assert c.get("/health/live").status_code == 200
        response = c.get("/v1/search", params={"q": "sınaq müddəti"})
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"
        ok = c.get("/v1/search", params={"q": "sınaq müddəti"}, headers=bearer(token()))
        assert ok.status_code == 200


def test_the_tenant_comes_from_the_token_never_from_the_header() -> None:
    c, budget = client()
    with c:
        c.post("/v1/ask", json=ASK, headers=bearer(token()) | {"X-Tenant-Id": "victim"})
    assert budget.checked == ["acme"]


def test_spending_endpoints_need_the_ask_scope() -> None:
    c, budget = client()
    with c:
        response = c.post("/v1/ask", json=ASK, headers=bearer(token(scopes=[])))
        agent = c.post("/v1/agent/ask", json=ASK, headers=bearer(token(scopes=[])))
    assert response.status_code == agent.status_code == 403
    assert budget.checked == []  # refused before anything was spent


def test_reviews_need_the_review_scope_when_auth_is_on() -> None:
    c, _ = client()
    with c:
        assert c.get("/v1/reviews", headers=bearer(token(scopes=["ask"]))).status_code in {
            403,
            404,  # the fake services have no quality loop: the queue does not exist
        }


def test_without_auth_the_header_tenant_is_used_as_before() -> None:
    c, budget = client(AuthSettings())
    with c:
        assert c.post("/v1/ask", json=ASK, headers={"X-Tenant-Id": "dev"}).status_code == 200
    assert budget.checked == ["dev"]


def test_requests_over_the_limit_get_429_with_retry_after() -> None:
    c, _ = client(per_minute=2)
    headers = bearer(token())
    with c:
        codes = [
            c.get("/v1/search", params={"q": "sınaq"}, headers=headers).status_code
            for _ in range(3)
        ]
        other = c.get("/v1/search", params={"q": "sınaq"}, headers=bearer(token(subject="bob")))
        last = c.get("/v1/search", params={"q": "sınaq"}, headers=headers)
    assert codes == [200, 200, 429]
    assert other.status_code == 200  # limits are per subject
    assert 1 <= int(last.headers["Retry-After"]) <= 60


def test_every_v1_request_is_audited_with_its_outcome() -> None:
    rows: list[AuditRecord] = []
    c, _ = client(per_minute=3, audit=rows)  # the refused /v1/ask counts too
    with c:
        c.get("/v1/search", params={"q": "sınaq"})
        c.post("/v1/ask", json=ASK, headers=bearer(token(scopes=[])))
        for _ in range(3):
            c.get("/v1/search", params={"q": "sınaq"}, headers=bearer(token()))
        c.get("/health/live")
    got = [(r.subject, r.tenant, r.path, r.status, r.outcome) for r in rows]
    assert got == [
        (None, None, "/v1/search", 401, "missing_token"),
        ("alice", "acme", "/v1/ask", 403, "forbidden"),
        ("alice", "acme", "/v1/search", 200, "ok"),
        ("alice", "acme", "/v1/search", 200, "ok"),
        ("alice", "acme", "/v1/search", 429, "rate_limited"),
    ]


async def test_the_audit_queue_drops_rather_than_blocks() -> None:
    written: list[AuditRecord] = []

    async def write(rows: list[AuditRecord]) -> None:
        written.extend(rows)

    log = AuditLog(write, max_queue=2)
    rec = AuditRecord(None, None, "GET", "/v1/x", 200, "ok", None, 1.0)
    for _ in range(3):  # no await in between: the drain task has not run yet
        log.record(rec)
    assert log.dropped == 1
    await asyncio.sleep(0)
    await log.close()
    assert len(written) == 2
