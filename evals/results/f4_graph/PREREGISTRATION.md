# F4 graph retrieval: pre-registration (2026-10-18)

Written and committed **before** running anything on `evals/datasets/v1/multihop_b.jsonl`.

## What happened before this file

1. Protocol 1 (in `mizan_graph/evaluate.py`): select the policy with the best Recall@5 on the
   35 `multi` questions of v1 among policies not significantly worse overall, then report it
   on `multihop.jsonl`.
2. It selected `graph both seeds=2 boost=0.05`. On `multihop.jsonl` it changed nothing
   (Recall@5 0.838 -> 0.838) and lowered MRR (0.911 -> 0.870, significant).
3. The full grid showed why: a cited article is typically 0.1-0.3 below the seed in cosine,
   so a boost small enough to leave other rankings alone cannot bring it into the top 5.
   Reserving slots works: `out seeds=1 slots=1/5` gave 0.912 and `slots=2/5` 0.925 on
   `multihop.jsonl`, with v1 unchanged (Recall@5 0.950 / 0.948 vs 0.948, MRR 0.901 vs 0.894).
4. Choosing among 28 policies by 35 questions picked noise (winner's curse). And since the
   slot result was seen on `multihop.jsonl`, that set is no longer held out.

## Registered now

- **Primary policy:** `GraphPolicy(seeds=1, direction=out, slots=1, window=5,
  route_citations=True)`. Preferred over `slots=2` because v1 (the tuning set) prefers it
  (multi Recall@5 0.867 vs 0.852) and it displaces one dense candidate instead of two.
- **Test set:** `multihop_b.jsonl`, 30 new questions written from citation edges whose article
  pairs are not in `multihop.jsonl`, before any retrieval was run on them.
- **F4 DoD criterion:** Recall@5 of the primary policy on `multihop_b` is at least 10%
  (relative) above dense retrieval, and the primary policy is not significantly worse than
  dense on any metric of v1 (paired bootstrap, 95%).
- `slots=2` is reported as a secondary result only; it cannot satisfy the DoD.
- The result is reported whichever way it goes.
