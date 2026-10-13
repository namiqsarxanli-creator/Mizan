# ADR-0003: Temporal versioning of legal texts

- **Status:** Accepted
- **Date:** 2026-10-13
- **Phase:** F1

## Context
A legal answer is only correct relative to a date. "Can my employer dismiss me without
notice?" has a different answer for a dismissal in 2020 than in 2026: Article 69.1 of the Labour
Code changed on 2025-06-13. Mizan must answer "what did the law say on date D?" exactly, cite
the text that was in force, and show how a provision evolved (F4).

What the source (e-qanun.az, Ministry of Justice) gives us, found while building F1:

- `getVersions` lists **consolidated snapshots**, each with an `effectDate`: the date that
  text came into force. The Labour Code has 13 of them, from the 1999 original to 2026.
- The **current** text is published separately and has **no date**: it already contains
  amendments newer than the newest dated snapshot.
- Each snapshot carries **endnotes** naming the amending act (number and adoption date) for
  every change. They are reliable as history, but the format drifts over 25 years.
- Files are Word "Save as HTML" exports in mixed encodings (windows-1251 *and* windows-1254
  declared as 1251), with layout instead of structure, and some old exports use `<h3>` headings
  and small-caps casing artefacts.

## Options considered
| Option | Pros | Cons |
|---|---|---|
| Store only the current text | Simplest | Wrong answers for any past date; no history for F4 |
| **Snapshot per version** + content-addressed text | Mirrors the source exactly; "as of D" is one indexed range lookup; trivially correct | Repeats structure rows per version |
| Provision-level intervals (one row per provision per period of unchanged text) | Compact; direct "history of article X" | Must infer *which* provision changed by diffing; renumbering and inserted articles ("7-1") make it fragile; a diff bug corrupts history silently |
| Reconstruct versions from amending acts | Finest granularity | Amending acts are prose ("words X are replaced by Y"); a research project of its own |

## Decision
**One row per consolidated version, valid on `[valid_from, valid_to)`, with provision text
stored content-addressed.**

- `document_versions` holds the interval. `valid_to` is always derived (= next version's
  `valid_from`), never entered, so intervals cannot overlap or leave gaps.
- `provisions` holds each version's structure under **stable keys**
  (`art:69/part:3-1/item:a`). Article keys do not include the chapter, so renumbered chapters do
  not break identity across versions.
- `provision_texts` is keyed by SHA-256 of the normalised content. Unchanged provisions are stored
  once: on the Labour Code, **23,101 provision rows share 2,487 texts (89% deduplication)**,
  and "did article X change between two versions?" is a hash comparison.
- **The undated current text** gets `valid_from` = the later of (adoption date of its newest
  amending act) and (newest dated version + 1 day), flagged `valid_from_estimated = true`. When
  e-qanun later archives that text as a dated version, its SHA matches and the real date
  replaces the estimate (covered by an integration test).
- **Idempotency:** a version's identity is the SHA-256 of its raw bytes. Re-running a sync
  downloads only the current text (historical files are immutable and cached on disk), parses
  nothing already parsed by the current `PARSER_VERSION`, and writes nothing. Bumping
  `PARSER_VERSION` re-parses stored versions in place and garbage-collects orphaned texts.
- **Encoding** is detected per file: strict UTF-8 first, otherwise the single-byte code page
  that yields the fewest Cyrillic letters, since genuine Azerbaijani text has almost none.

## Consequences
- "Article 69 as of 2021-06-01" is a single query, `mizan-ingest as-of 46943 2021-06-01 --key art:69`.
- Granularity is limited to the source's snapshots: two amendments that came into force between
  two snapshots appear as one change. Acceptable, since this is how the official consolidated
  text is published; the endnotes still name every amending act.
- The current version's start date is an estimate, and we say so in data rather than hiding it.
  Answers that depend on that boundary can surface the uncertainty.
- Embedding and indexing (originally listed under F1) move to F2, where the embedding model is
  chosen by evaluation; F1 delivers structured, versioned text plus chunking strategies.
- **How we will know it was wrong:** if F4's per-provision history views need diffing so often
  that it dominates query time, we add a materialised provision-interval table *derived* from
  these snapshots (snapshots stay the source of truth).
