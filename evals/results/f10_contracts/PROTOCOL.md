# F10 contract analysis: protocol for the real-contract test

Written on 2026-10-31, before any real contract was seen. The synthetic set
(`evals/datasets/contracts/v1`) is the development set. The rules and prompts were written
against it, so its scores show coverage, not generalisation. The test is a set of real
employment contracts, scored once as described here.

## Data
- **Count:** at least 10 contracts actually used by employers in Azerbaijan, from different
  sectors.
- **Anonymisation:** before the contracts reach the repository machine, remove names, FIN, ID
  card numbers, addresses, phone numbers and bank accounts. Company names and salaries are
  removed if the provider wishes. Legal terms stay.
- **Storage:** contracts live in `evals/datasets/contracts/real/`, which is git-ignored. Only
  aggregate metrics and per-clause verdicts without clause text are committed.

## Labels (before the system is run)
1. The contracts are split into clauses with `split_clauses`. Splitting is mechanical and
   produces the clause numbers to label.
2. Each clause gets `ok` / `violation` / `risk`, and for a violation the article(s), as of the
   contract's analysis date.
   - **Labeller:** a lawyer if one is available; otherwise the project author. The author is
     then a weaker labeller, and that is stated in the report.
   - **Isolation:** the labeller does not see the system's output first.
3. Labels go to `real/labels.jsonl`, in the format of the synthetic `labels.jsonl`.

## Freeze
- The pipeline is frozen at the commit that adds this file's "frozen at" line:
  - rules;
  - prompt;
  - batch size;
  - retrieval configuration;
  - review model.
- No rule or prompt changes before scoring.

## Scoring (once)
- **Command:** `mizan-eval contracts --llm` pointed at the real set.
- **Same metrics as on dev, per layer** (rules, LLM, combined):
  - precision, recall and F1 for `violation`;
  - right-article rate;
  - false positives on lawful clauses;
  - unverified findings dropped.
- `risk` labels and predictions are reported separately and are not counted as violations.

## What counts as "works correctly" (the DoD)
On the real set, the combined pipeline must reach:

| Requirement | Threshold |
|---|---|
| Precision for `violation` | ≥ 0.80 (a flagged clause is usually a real problem) |
| Recall for `violation` | ≥ 0.70 |
| Right article, among detected violations | ≥ 0.80 |
| Contracts failing to parse into clauses | none (each yields ≥ 80% of the labeller's clauses) |

The thresholds are a choice made now, before seeing the data. With about 10 contracts and
perhaps 15–30 violations, the confidence intervals will be wide; they are reported with the
point estimates.

## After scoring
- Every miss and false positive is listed, with its cause: rule gap, retrieval, model, OCR, or
  splitting.
- Fixes made afterwards are reported as post-hoc and do not replace the frozen result. This
  follows the F10 red-team report.

## Amendment 1 (2026-10-31, before any test contract exists): an independent-author test set
Real contracts are not available: the project owner cannot obtain any. The test set is
therefore **synthetic, written by a different author**:

- **Author.** Another model (OpenAI Codex), working from
  [`evals/datasets/contracts/test/BRIEF.md`](../../datasets/contracts/test/BRIEF.md). It has
  not seen the rules, the review prompt or the development set.
- **Labels.** The author labels its own clauses: `ok` / `violation` / `risk`, with articles.
- **Label check.** Before the system runs, the project author checks every `violation` and
  every hard-lawful label against the text of the cited article.
  - Only clear legal errors are corrected: a wrong article number, or a limit the Code does
    not state.
  - Each correction is logged in `test/LABEL_CHANGES.md` with its reason.
  - The check reads the Code and the labels only, never the system's output.
- **Storage.** The set lives in `evals/datasets/contracts/test/`: `contracts/*.txt` and
  `labels.jsonl`. It is committed, since nothing in it is private.
- **Scoring:** `mizan-eval contracts --llm --dataset evals/datasets/contracts/test --out
  evals/results/f10_contracts/test`, run once. The thresholds above apply unchanged.

**What this changes in the claim.**

- The DoD said "10 real samples". This set tests generalisation to another author's wording
  and choice of violations. It does not test real-world drafting, scans or OCR.
- The report states this as a deviation, not as the DoD being met.

Frozen at: `eb4257f` (2026-10-31). The rules, review prompt, batch size, retrieval
configuration and model are those of that commit. The evaluation harness gained only a
`--dataset` option afterwards.

## Amendment 2 (2026-10-31, before the scored run produced any result)
Groq's free daily token limit cannot cover the test set in one day. The review model is
therefore served by a chain of providers (`FailoverClient`):

1. Groq first.
2. OpenRouter for the rest, once Groq rate-limits. OpenRouter's paid tier costs about $0.05
   for this run.

The model is the same open-weight `gpt-oss-120b`, with the same prompt, batch size,
retrieval and rules. Cerebras offers it too, but asks for payment (HTTP 402), so it is not in
the chain.

- **What may differ:** the serving stack. Quantisation and sampling details can vary by
  provider.
- **Reporting:** the report records which providers served the run.
- A run started on Groq alone was stopped before any contract finished, so no test result
  exists from it.
