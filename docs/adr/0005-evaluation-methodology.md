# ADR-0005: Evaluation methodology and the CI eval gate

- **Status:** Accepted
- **Date:** 2026-10-16
- **Phase:** F3

## Context
F2 chose the retrieval configuration by measurement (ADR-0004). Two gaps remained:

1. **Nothing protected that result.** A refactor, a parser change or a new model could silently
   lower retrieval quality; unit tests check behaviour, not quality.
2. **Retrieval quality is not answer quality.** In F2's end-to-end check the right article was
   retrieved and the model still answered from the wrong one, with a verbatim (so "valid")
   quotation. Nothing measured the answers themselves.

Constraints: a laptop CPU and GitHub-hosted runners (no GPU); a free LLM tier limited to 8k
tokens per minute per model; no lawyer yet; a public-service source (e-qanun.az) that CI must
not hit on every push.

## Decision

### 1. Datasets are versioned, documented and checked
`evals/datasets/v0` (70 questions) and `v1` (200 questions, six question types, see its
README). Every label is resolved against the corpus on the question's date, and false-premise
facts are found in the gold text, before a dataset is committed. Datasets are never used for
training (F7/F8) to keep evaluation honest.

### 2. Metrics: mechanical where possible, judged only where necessary
| Metric | How | Why |
|---|---|---|
| Recall@5, MRR@10, nDCG@10, Hit@k | article-level ranks | retrieval quality |
| `citation_valid` | quote found verbatim in its source (code) | cheap hallucination guard |
| `cites_gold` | a verified citation points into a gold article (code) | catches "right quote, wrong article" |
| `answer_correct` | LLM judge vs. the gold text in force on the date, rules per question type | the only property code cannot check |
| abstention accuracy | judge, on `no_answer` questions | refusing out-of-scope questions is a feature |

Every rate carries a 95% bootstrap CI. Comparisons between two systems use a **paired**
bootstrap over the same questions: pairing removes between-question variance, so 70-200
questions can detect real changes without flagging noise.

### 3. The judge is a measurement instrument, and gets calibrated
- Different model from the answerer (`gpt-oss-20b` judging `gpt-oss-120b`; self-preference
  bias is well documented), temperature 0, JSON output, `JUDGE_VERSION` stored with every
  verdict so prompt changes invalidate old verdicts.
- It sees the gold provisions *as of the question's date*, the question type's rule, and the
  answer; not which system produced it.
- **Calibration:** humans label a sample blind (`mizan-eval label` never shows the verdict);
  agreement is Cohen's kappa, which discounts chance agreement. Target **kappa >= 0.6**
  (substantial). Until it is met, judged numbers are reported as uncalibrated.

### 4. The CI gate protects retrieval on every change
- A **frozen corpus snapshot** (`evals/snapshots/labour_code.jsonl.gz`, 1,578 records, 250 KB,
  deterministic bytes) lets CI build the index without Postgres or e-qanun.az.
- bge-m3 and the passage embeddings are cached (the latter keyed by the snapshot's hash), so
  the job re-embeds only when the corpus changes.
- `mizan-retrieval gate` evaluates the serving configuration on v0 and compares it with
  `evals/baselines/retrieval.json`: it **fails if any metric's paired-difference interval lies
  entirely below zero**, or Recall@5 drops under the 0.75 floor. Verified both ways: the serving
  configuration passes with delta 0.000; BM25-only fails at -0.336 [-0.450, -0.226].
- Baselines are regenerated deliberately (`--update-baseline`), in a commit that explains why;
  the gate refuses to compare against a baseline built on a different dataset.

### 5. Generation eval runs offline, not per push
Answering 200 questions at 8k tokens/minute takes hours, and its cost and rate limits do not
belong in every CI run. `mizan-eval run` is paced, resumable (one checkpoint line per
question) and samples by type; it runs before releases and after changes to prompts, models or
answering code.

## Consequences
- Regressions in retrieval are caught at review time with a readable table in the job
  summary, at ~3 minutes per run once caches are warm.
- Answer quality now has numbers, including the failure mode F2 found (`cites_gold`).
- The weakest link is explicit: the judge is uncalibrated until humans label; the datasets are
  silver until a lawyer reviews them.
- **How we will know it was wrong:** the gate flagging changes that reviewers consider noise
  (interval too tight), or missing regressions found later in generation eval (gate measures
  the wrong thing); kappa staying below 0.6 after prompt iteration (the judge cannot be trusted
  for this task and human review must replace it).
