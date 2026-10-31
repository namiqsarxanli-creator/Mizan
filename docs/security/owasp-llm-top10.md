# OWASP Top 10 for LLM Applications (2025): Mizan audit

- **Date:** 2026-10-30
- **Scope:** the HTTP API, the agent, the MCP server, the ingestion pipeline and the training
  data path, as of commit `a33dbbc` and the F10 changes after it.
- **Status values:**
  - ✅ controlled: a mechanism exists and is tested;
  - 🟡 partial: a mechanism exists with a known gap;
  - ⬜ open.

| # | Risk | Status | Controls in Mizan | Gaps and next steps |
|---|---|---|---|---|
| LLM01 | Prompt injection | ✅ | Screen on every question (CI gate: 98.2% of test attacks blocked, 0/281 false positives; [red-team report](../../evals/results/f10_redteam/README.md)). Untrusted text neutralised; sources delimited as data in every prompt. JSON schema outputs; quotes checked mechanically against sources. Model resisted 20/20 attacks that bypassed the screen. | The screen is pattern-based, so novel paraphrases pass and depend on the lower layers. Contract uploads (indirect injection) are screened clause by clause (`channel="document"`); a flagged clause is reported and never sent to a model (ADR-0013). |
| LLM02 | Sensitive information disclosure | ✅ | Questions stored redacted (FIN, phone, e-mail; `mizan_core.genai.redact`). Postgres row-level security on answers: another tenant's rows are invisible ([ADR-0012](../adr/0012-tenancy-and-access.md)). Tenant taken from the token only. Answers reproducing a system prompt replaced by a refusal. No secrets in prompts. | The API logs in to Postgres as the owner and switches role per transaction. Production should log in as `mizan_app`. |
| LLM03 | Supply chain | 🟡 | `uv.lock` pins every dependency, installed `--frozen` in CI. Models pinned by Hugging Face id. ruff bandit rules (`S`) in CI. Docker images pinned to versions. | No automated vulnerability scan (`pip-audit`, Dependabot) and no model hash pinning (Hugging Face revisions). Both are cheap to add. |
| LLM04 | Data and model poisoning | 🟡 | The corpus comes from one official source (e-qanun.az), versioned and content-addressed (ADR-0003). Fine-tuning data is generated, filtered by a judge from another model family, and split by article. Review-queue corrections reach training data only after a person decides. | Ingested text is not diffed against an independent copy. A compromised source would be ingested as-is; the amendment history makes it visible, not blocked. |
| LLM05 | Improper output handling | ✅ | Outputs are parsed as JSON against schemas; invalid output becomes a refusal. Citations are kept only when their quote is found in the source. The API returns JSON, never HTML; the MCP server returns text. The model never builds SQL, shell commands or URLs we act on. | A future UI must render answers as text, not HTML or markdown with images (exfiltration by image URL). |
| LLM06 | Excessive agency | ✅ | The agent has four read-only tools (search, article, decisions, conflicts) and no write, network or code execution. MCP tools are annotated `read_only_hint`. Each run has a token budget, an iteration cap, step timeouts and a parallelism cap (ADR-0007). | None at this scope. |
| LLM07 | System prompt leakage | ✅ | Prompts hold no secrets or credentials, by design. Extraction attempts are screened; verbatim reproduction of a prompt is blocked on output (`guard.leaked`). | Prompts are in the public repository. Leakage would reveal nothing confidential; the controls stop it being used as a lever. |
| LLM08 | Vector and embedding weaknesses | ✅ | Qdrant holds only the public legal corpus. No user data is embedded, except in the semantic cache, which is off by default. Each cache entry is checked against fresh retrieval (evidence fingerprint), so a poisoned entry cannot outlive its sources. Qdrant is bound to 127.0.0.1. | If the cache is turned on for many tenants, its entries should be scoped per tenant. They are global today, which is acceptable for public-law answers. |
| LLM09 | Misinformation | ✅ | Answers cite the provision and the date in force. Each claim is verified against its quote. Low confidence abstains (selective prediction). Low-confidence, unfaithful and 👎 answers go to a human review queue. Online faithfulness evaluation with drift alerts. A disclaimer ("not legal advice") in the README and the prompts. | The contract-analysis page and every contract report carry the disclaimer; the question endpoints have no UI yet. |
| LLM10 | Unbounded consumption | ✅ | Daily token budget per tenant (429 with `Retry-After`). Per-subject rate limit per minute. Per-run token budget and timeouts in the agent. Input length limits (question ≤ 1000 characters). Idempotency keys stop retried agent runs from paying twice (ADR-0011). | No global (all tenants) spend cap. The provider's own limits are the backstop today. |

## Summary
- **Controlled:** 8 of 10.
- **Partial:** 2.
  - Supply chain: no vulnerability scanning yet.
  - Poisoning: no independent check of the source.
- **Cheapest next steps:**
  1. `pip-audit` in CI, plus Dependabot for actions and Docker images.
  2. Pin Hugging Face model revisions.
  3. The API's Postgres login as `mizan_app`.
  4. Render answers as plain text in any future UI (the contract page already does).
