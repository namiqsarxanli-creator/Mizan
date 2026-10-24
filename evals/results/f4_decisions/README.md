# F4: Constitutional Court decisions in the answer context

Dataset: `evals/datasets/v1/court_decisions.jsonl` (11 questions whose correct answer depends
on a Constitutional Court decision; `cd11` repeats `cd01` on 2025-12-01, before the decision).
Both runs use the serving search (dense + graph); the only difference is whether the decisions
interpreting the retrieved articles are added to the context. The judge (gpt-oss-20b) sees the
same gold in both runs: the articles, the decisions in force on the date, and an expected
conclusion written from the decision's operative part.

| | Without decisions | With decisions |
|---|---|---|
| Answer correct | 0.273 [0.00, 0.55] | **0.909** [0.73, 1.00] |
| Paired difference | | **+0.636** [+0.36, +0.91]: 7 questions fixed, 0 broken |

- **Temporal control:** `cd11` (before the decision) gets no decision in context and is
  answered with the old rule in both runs. Decisions enter only from their adoption date.
- **Failure without decisions:** the answers apply the literal text that the Court changed.
  Examples: no compensation for unused additional leave (Art. 144(3), suspended in 2026); a 20%
  deduction limit where the Court allows 50% by voluntary agreement; no fixed-term contract by
  mere agreement where the Court upheld Art. 47 "f".
- **Remaining miss (`cd09`):** the answer makes an employment contract mandatory in every case.
  The decision instead leaves qualification of the relation to the courts.
- **Weakness:** answers often follow a decision without citing it (3 of 10 cite the `kmq:` key),
  and verbatim citation validity is 0.58. The F5 drafter requires a quote per claim.

Commands:

```bash
uv run mizan-eval run --dataset evals/datasets/v1/court_decisions.jsonl --sample 0 --graph --out evals/results/f4_decisions/without
uv run mizan-eval run --dataset evals/datasets/v1/court_decisions.jsonl --sample 0 --graph --decisions --out evals/results/f4_decisions/with
```
