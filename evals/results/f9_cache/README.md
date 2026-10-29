# F9: semantic answer cache

**Verdict: the saving gate missed narrowly. The cache saved 23.9% against a target of ≥ 25%,
and one near-duplicate false hit occurred. The cache is built, tested and wired into `/v1/ask`,
but it is off by default (`MIZAN_CACHE__ENABLED=false`).**

The protocol is in [PREREGISTRATION.md](PREREGISTRATION.md), committed before any paraphrase or
similarity existed. Numbers come from `mizan-eval cache` (`report.json`); the paraphrases are in
`evals/datasets/v1/paraphrases.jsonl`.

## Design (`mizan_retrieval.cache`)

- **Match:** cosine similarity of the question's bge-m3 embedding (the serving query encoder),
  stored in a Qdrant collection.
- **Temporal invalidation by evidence fingerprint:**
  - An entry stores the provisions its answer cites, each with the date its text took effect.
  - It is served only if each of them is retrieved again for the new request, in the same
    version.
  - An amendment, or a question about a date when another version was in force, misses
    automatically.
  - Retrieval runs on every request (about 0.2 s); the cache saves the LLM call.
- **Versioning:**
  - Entries are scoped by prompt version + model; refusals are not cached.
  - The TTL is 7 days as a backstop.
- **Response:** `cached: true` in the response. A cached answer costs the tenant's budget
  nothing.

## Calibration (185 v1 questions × 2 paraphrases, split in halves by question id)

| | calibration half | evaluation half |
|---|---|---|
| near-miss pairs (different questions, same gold article) | 24 | 32 |
| **threshold** (smallest with ≤ 1% false hits on calibration) | **0.9431** | – |
| paraphrase hit rate | 0.397 [0.33, 0.46] | **0.409 [0.34, 0.48]** |
| near-miss false-hit rate | 0.000 | **0.000 [0, 0]** |

## Workload (each evaluation question asked in its 3 phrasings, shuffled)

| | requests | LLM calls | cache hits | false hits | saving |
|---|---|---|---|---|---|
| with the evidence check (pre-registered) | 264 | 201 | 63 | 1 | **23.9%** |
| similarity only | 264 | 195 | 69 | 1 | 26.1% |
| ceiling (every repeat a hit) | | | | | 66.7% |

The one false hit:

- the stored question: "Hamilə qadını ixtisar adı ilə işdən çıxarmaq qanuni olaraq mümkündürmü?"
  (dismissing a pregnant woman *for redundancy*);
- the question it was served to: "Hamilə qadını işdən çıxarmaq qanuni olaraq mümkündürmü?";
- similarity 0.953; both have the same gold article (art:79).

The protocol counts this as a false hit because they are different questions. In substance it
is a near-duplicate: the narrower question's answer is almost the answer to the broader one.

## Reading

1. **Precision is fine, recall is the limit.**
   - At 0.943 no near-miss pair hits, but only 41% of paraphrases do.
   - People's paraphrases move bge-m3 embeddings more than the threshold allows.
   - A lower threshold would raise the saving, and also admit the same-article different-question
     pairs that the threshold exists to stop.
2. **The threshold is fragile.**
   - The calibration half has only 24 near-miss pairs, so the ≤ 1% rule reduces to "above the
     highest one".
   - A larger set of near misses is needed before the threshold can be trusted to two decimals.
3. **The evidence check costs little.**
   - It took 2.2 points of saving (26.1% → 23.9%).
   - That is the price of never serving an answer whose cited text has changed.
4. **The saving is a property of traffic, not of the cache.**
   - With repeat share *r* (requests that rephrase an earlier one) and hit rate *h* ≈ 0.41, the
     saving is about *r · h*.
   - 25% needs about 60% of traffic to be repeats.

## Why it ships off

The DoD (≥ 25%) was not met on the pre-registered workload, and its one false hit, however
benign, is the failure mode a legal answer cannot afford. Turning the cache on is a config
change (`MIZAN_CACHE__ENABLED=true`) once production traffic shows:

- a repeat rate high enough to matter;
- a false-hit audit on real questions.

The F6 quality loop already logs every answer for that audit.
