# F9 semantic cache — pre-registration

Written and committed **before** the paraphrases were generated or any similarity was computed.

## Question

At what cosine similarity of question embeddings (bge-m3, the serving query encoder) may Mizan
return a cached answer? And how much LLM cost does the cache then save?

## Data

- **Questions:** v1/questions.jsonl, those with gold articles (185).
- **Paraphrases:** `gpt-oss-120b` writes two paraphrases of each question that keep its meaning
  and its legal situation. They are stored in `evals/datasets/v1/paraphrases.jsonl`, so the
  study is reproducible.
- **Positive pairs (should hit):** (question, its paraphrase).
- **Near-miss negatives (must not hit):** pairs of *different* v1 questions that share a gold
  article. They are the hardest case, the same article with a different question, and the one
  that would return a wrong legal answer.
- **Split:** by question id hash. Half of the questions calibrate the threshold, half evaluate
  it. A pair belongs to a half when both of its questions do; mixed pairs are not used.

## Rule (fixed now)

- **Threshold:** the smallest threshold whose false-hit rate on the calibration negatives is
  ≤ 1%. This favours precision: a wrong legal answer costs more than an LLM call.
- **On the evaluation half, reported with 95% bootstrap intervals:**
  - paraphrase hit rate;
  - near-miss false-hit rate.

## Cost (the DoD: ≥ 25% lower cost)

The saving depends on how often users repeat themselves, which we have no traffic to measure.
It is therefore reported for a stated workload:

- Each evaluation question is asked in its three phrasings, in a random order.
- The first phrasing is a miss; each later one hits if its similarity to an earlier phrasing is
  ≥ threshold **and** the earlier phrasing's gold articles are retrieved again for it. The second
  condition is a proxy for the evidence-fingerprint check.
- **Saving** is the share of requests answered from the cache (no LLM call). The ceiling is 2/3.
- **Pass if the saving is ≥ 25%** on this workload. A false hit counts as a hit for the saving
  and is reported separately.
- A real deployment saves in proportion to its repeat rate; the report gives that formula as well.
