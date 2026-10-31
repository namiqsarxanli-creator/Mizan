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

Frozen at: _(set when the contracts arrive)_
