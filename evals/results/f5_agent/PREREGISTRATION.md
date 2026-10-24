# F5 agent eval: pre-registration (2026-10-25)

Written and committed before the eval below was run.

## Systems
- **baseline:** the serving `/v1/ask` pipeline. Dense search + citation graph, whole articles,
  Constitutional Court decisions in the context, one `gpt-oss-120b` answer.
- **agent:** `mizan_agents.Orchestrator` with its committed defaults (`Limits()`):
  planner and verifier `gpt-oss-20b`, drafter `gpt-oss-120b`, 2 iterations, abstain below
  confidence 0.5, token budget 40k.

## Questions (51)
- 40 from v1 by `stratified_sample(seed=13)`: 20 single, 7 multi, 4 exact_term, 4 temporal,
  3 no_answer, 2 false_premise.
- The 11 court-decision questions.

Development used only `q001` and `q002` (a 2-question smoke test). They are not in the sample.

## Metrics
| Metric | Definition |
|---|---|
| `correct` | F3 judge (`gpt-oss-20b`) against the gold in force on the date; abstaining on an in-scope question is incorrect |
| `faithfulness` | `qwen3.8-27b` lists the answer's legal statements and checks each against the sources the system was given; computed on answered questions |
| `citation_accuracy` | share of citations whose quote occurs verbatim (elisions allowed) in the cited source; on answered questions |
| `coverage`, `selective_accuracy` | share answered (in scope); accuracy on answered |

## F5 DoD, decided on these numbers whichever way they go
1. Agent faithfulness ≥ 0.90.
2. Agent citation accuracy ≥ 0.90.
3. Agent `correct` better than baseline: paired bootstrap 95% lower bound > 0 over the 51
   questions.

Known limitation: the F3 judge is not yet calibrated against human labels (κ pending). Both
systems are graded by the same judge, so the paired comparison is fair even if the judge is
strict.
