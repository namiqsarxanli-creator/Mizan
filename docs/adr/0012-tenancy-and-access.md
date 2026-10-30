# ADR-0012: Tenancy and access: JWT identity, row-level security, rate limits, audit log

- **Status:** Accepted
- **Date:** 2026-10-30
- **Phase:** F9–F10

## Context
Until F10, a caller named its own tenant in the `X-Tenant-Id` header. That header decided two
things: whose daily token budget was charged, and whose answers and feedback a request could
touch. Anyone could therefore:

- spend another tenant's budget;
- send feedback on its answers;
- run the LLM endpoints without limit.

Nothing recorded who did what. F10 asks for authentication, rate limiting, an audit log, and
data isolation per tenant.

## Options considered
| Option | Pros | Cons |
|---|---|---|
| A. Static API keys in a table | simple to revoke one key | a database lookup per request; a key store and rotation to build |
| B. JWT (HS256) issued by us, verified statelessly | no lookup; tenant and scopes travel in the token; standard tooling | revoking one token before it expires needs a deny-list or short lifetimes |
| C. OAuth 2 / OIDC with an external identity provider | single sign-on, MFA, revocation for free | an IdP to run or pay for; no users to sign in yet |
| D. Isolation in application code only (`WHERE tenant = …`) | no database features | one forgotten filter leaks data |
| E. Postgres row-level security as well | the database refuses cross-tenant rows even when the code forgets | a role switch per transaction |

## Decision
**B now, shaped so that C replaces only the token check. Isolation is D and E.**

1. **Identity.** Every `/v1` request needs `Authorization: Bearer <JWT>`.
   - **Checks:** HS256 signature, our issuer and audience, an expiry, and a well-formed tenant.
     The algorithm is fixed by the server, never read from the token (`alg: none` is refused).
   - **Claims:** subject, tenant and scopes come from the token. `X-Tenant-Id` is ignored when
     auth is on.
   - **Scopes:** `ask` for the endpoints that spend LLM tokens, `review` for the review queue.
     Search and provision lookups need only a valid token. The check happens before anything
     is spent (403 before 503).
   - **Issuing:** operators hold the secret and issue tokens with `mizan-token`.
   - **Moving to C:** an OIDC provider would replace `verify_token` (RS256 with the provider's
     JWKS). The scopes and the rest stay as they are.
2. **Settings guards.**
   - Staging and production refuse to start without auth.
   - The secret must be at least 32 characters (RFC 7518 §3.2).
   - Off is for local development only; the tenant then comes from the header as before.
3. **Rate limit.** At most N requests per minute per subject, in fixed Redis windows (one
   `INCR` per request); 429 with `Retry-After`.
   - The daily token budget per tenant (F5) bounds cost; this limit bounds request rate.
   - Refused requests count toward the limit too.
4. **Audit log.** One `audit_log` row per `/v1` request, refused ones included.
   - **Fields:** subject, tenant, method, path, status, outcome (`ok`, `missing_token`,
     `invalid_token`, `forbidden`, `rate_limited`), trace id, latency.
   - **Writes:** a background task inserts the rows in batches, so the database is never on
     the request path. A full queue drops rows and logs the count rather than slowing requests.
   - **Append-only:** `mizan_app` may insert and read but not update or delete rows (tested).
   - **No bodies:** questions are already stored redacted in `answers`, and the trace id joins
     the two.
5. **Row-level security** on `answers` (F9 migration `e5b8c2d4a917`).
   - Each transaction runs as `mizan_app` (`SET LOCAL ROLE`) with `mizan.tenant` set; other
     tenants' rows are invisible and cannot be written.
   - An unset tenant sees nothing; `*` is the reviewer and analytics scope.

## Consequences
- **Easier:** tenants cannot spend or read each other's resources, even through a bug in a
  query. Every request leaves a trace an operator can follow.
- **Easier:** the MCP server sends `MIZAN_API_TOKEN`, so desktop clients work with auth on.
- **Harder, accepted:** one token cannot be revoked before it expires. Issue short-lived
  tokens, and rotate the secret to revoke them all. A per-token deny-list (`jti` in Redis)
  can be added if needed.
- **Harder, accepted:** fixed windows allow up to 2N requests across a window boundary. A
  sliding window costs one more Redis call per request and is not needed at this scale.
- **Open:** the API still logs in to Postgres as the owner and switches role per transaction.
  In production it should log in as `mizan_app` itself, so that a missing `as_tenant` fails
  closed.
- **Open:** OAuth/OIDC (option C) waits for real users.

**How we will know it was wrong:**
- The need to revoke individual tokens comes up in practice. Move to option A or C.
- Audit rows are dropped under normal load (`audit_dropped` in the logs). Raise the queue size
  or batch size, or ship the rows through the OTel logs pipeline instead.
