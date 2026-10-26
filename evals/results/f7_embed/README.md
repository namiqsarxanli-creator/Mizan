# F7: fine-tuned embeddings (bge-m3 → bge-m3-mizan)

**Verdict: the gate failed. The fine-tuned model is not promoted; serving stays on `bge-m3`.**

## What was trained

- **Data:** synthetic questions for 218 of 329 Labour Code articles (`mizan-train synth`).
  - Generators: Gemini 3 Flash / 2.5 Flash. Round-trip judge: Gemini 2.5 Flash-Lite, from a
    different family than the generator.
  - 866 pairs; 83 rejected by the judge; 4 dropped by the leakage guard (char 3-gram Jaccard ≥ 0.5
    against every eval question).
  - Article-level split: 603 train / 111 val / 65 test questions (166 / 31 / 20 articles).
- **Hard negatives:** dense top-40 with the article's citation neighbours excluded (`mizan-train build`).
- **Training:**
  - Full fine-tuning of BAAI/bge-m3 (568M parameters) with MNRL, in-batch negatives, one hard
    negative and same-article masking.
  - bf16 autocast with gradient checkpointing. Batch 32.
  - Hardware: one RTX 3090 (RunPod community, $0.22/h). The pod ran about 37 minutes, about $0.14.
- **Selection** (decided before looking at test): best validation MRR@10 over two runs.

| run | best epoch | val MRR@10 | val R@1 | val R@5 |
|---|---|---|---|---|
| base | – | 0.726 | 0.595 | 0.901 |
| lr 2e-5, 3 epochs | 3 | 0.781 | 0.658 | 0.955 |
| **lr 1e-5, 4 epochs** (selected) | 1 | **0.806** | 0.694 | 0.937 |

**Held-out synthetic test split** (65 questions, articles unseen in training):

| | R@1 | R@5 | MRR@10 |
|---|---|---|---|
| base | 0.554 | 0.862 | 0.685 |
| bge-m3-mizan | 0.662 | 0.908 | 0.773 |

## The gate: the human-written eval sets

The gate is fixed in `mizan_train.cli` (`PRIMARY_SET`, `MIN_GAIN`). The candidate passes only
if, on `v1/questions.jsonl` (dense, article strategy), **Recall@5 rises by at least 8% relative**
to the base model.

Run with `mizan-train compare`. Deltas are paired bootstrap 95% intervals; full output is in
`compare.json`.

| set | n | Recall@5 base → tuned | Δ [95% CI] | MRR@10 base → tuned | Δ [95% CI] |
|---|---|---|---|---|---|
| v1/questions (primary) | 185 | 0.946 → 0.950 | +0.005 [−0.02, 0.03] | 0.894 → 0.852 | **−0.042 [−0.08, −0.01]** |
| v0/retrieval | 70 | 0.929 → 0.933 | +0.005 [−0.05, 0.06] | 0.831 → 0.835 | +0.003 [−0.07, 0.08] |
| v1/multihop | 40 | 0.838 → 0.787 | −0.050 [−0.13, 0.03] | 0.911 → 0.901 | −0.010 [−0.06, 0.04] |
| v1/multihop_b | 30 | 0.700 → 0.800 | **+0.100 [0.02, 0.18]** | 0.836 → 0.928 | +0.092 [−0.01, 0.21] |
| v1/court_decisions | 11 | 0.955 → 0.955 | 0 | 0.848 → 0.909 | +0.061 [−0.09, 0.23] |

The relative Recall@5 gain on the primary set is **+0.4%**, against a target of ≥ 8%, so the
gate failed.

## Reading

1. **Synthetic gains did not transfer.** On held-out synthetic questions the model improves a lot
   (MRR@10 +0.09). On questions written by people it is flat on Recall@5 and *worse* on ranking
   (MRR@10 −0.042, significant) on the primary set. The model learned the style of LLM-generated
   questions, which are cleaner and closer to the article's own wording, more than it learned the
   legal mapping. This is the classic synthetic-to-real gap. The held-out synthetic split alone
   would have shipped a regression.
2. **The target was badly posed, and that is our error.** Base Recall@5 on the primary set is
   already 0.946, so +8% relative would need 1.02, which is impossible. The gate could never
   pass on this metric, whatever the model. We report the failure as is and do not move the goal
   after seeing the data. The next gate will be set on a metric with headroom: MRR@10, or
   Recall@5 on the multi-hop sets.
3. **One positive signal, not acted on.** multihop_b improves significantly on Recall@5 (+0.10).
   It is a 30-question set and was one of five sets looked at, so with five sets checked it is
   not grounds for promotion (multiple comparisons).

## Next steps (not done)

- Generate questions in the style of real users: colloquial, with spelling errors, describing a
  situation rather than naming the rule. Mix in the question style of the eval sets' *training*
  counterparts, never the eval questions themselves.
- Cover the remaining 111 articles. Today the free generator quotas ran out.
- Pre-register a gate with headroom before the next run.

## Reproduce

```bash
mizan-train synth --batch 8      # resumable; free-tier quotas
mizan-train build                # needs Qdrant
python infra/gpu/make_bundle.py bundle   # copy bundle/ to a GPU box, then there:
python pod_train.py full-lr1e5 0 1e-5 4 32 && python pod_test.py full-lr1e5
mizan-retrieval index --strategy article --from-snapshot evals/snapshots/labour_code.jsonl.gz
mizan-train compare
```

The weights (`data/train/runs/bge-m3-mizan`, 1.1 GB in fp16) are a local artefact and are not
in git. `MIZAN_FINETUNED_EMBEDDINGS` points the registry at another copy.
