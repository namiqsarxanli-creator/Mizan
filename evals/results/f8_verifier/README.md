# F8: a local verifier (Qwen3.5-2B + LoRA) against the API verifier

Protocol: [`PREREGISTRATION.md`](PREREGISTRATION.md), committed before any data was generated or
model trained. Files:

- [`report.json`](report.json): the comparison, from `mizan-train verifier-report --gpu-usd-per-hour 0.22`;
- [`training.json`](training.json): the training curve;
- [`local_test_predictions.jsonl`](local_test_predictions.jsonl): the local model's test predictions.

**Result: both pre-registered gates pass.** The local verifier keeps 98.4% of the API
verifier's macro-F1 and costs 7.3 times less per claim. It is also 7 times faster per claim.

## Setup

- **Data:** 1,225 constructed claims that the judge kept, with labels known by construction
  (`restated` / `altered` / `absent` / `extended`).
  - The split is by article: 953 train, 158 val and 114 test claims.
  - The test claims come from 31 articles that no training claim touches.
- **Local model:** `Qwen/Qwen3.5-2B` (chosen in [`f8_tokenizers`](../f8_tokenizers/README.md)).
  - LoRA r=16 on all linear layers, 16.8M trainable parameters.
  - 3 epochs, lr 2e-4, loss on the answer tokens only.
  - Inference compares the next-token logits of the three labels: one forward pass per claim,
    and the output can never be unparseable.
- **Hardware:** a free Colab Tesla T4 (16 GB). The base weights are in fp16 and the adapters in
  fp32 under autocast with loss scaling, because a T4 has no bf16. Training took 59.5 minutes.
- **API model:** `gpt-oss-20b` on Groq with the production `VERIFIER_SYSTEM` prompt, one claim
  per call (`mizan-train verify-api`).

## Validation curve

The checkpoint was chosen on val; the test split was scored once.

| epoch | loss | val macro-F1 | val accuracy |
|---|---|---|---|
| 0 (base, no adapter) | – | 0.566 | 0.652 |
| 1 | 0.184 | 0.907 | 0.911 |
| 2 | 0.051 | 0.922 | 0.930 |
| **3 (kept)** | 0.017 | **0.935** | 0.943 |

## Test results (114 claims)

| | API (`gpt-oss-20b`) | local (Qwen3.5-2B + LoRA) |
|---|---|---|
| **macro-F1** | 0.951 | **0.935** (retention **0.984**; gate ≥ 0.95 ✅) |
| accuracy | 0.947 | 0.939 |
| F1 supported / contradicted / not_enough_info | 1.000 / 0.909 / 0.942 | 0.947 / 0.903 / 0.954 |
| accuracy on `restated` | 1.000 | 0.931 |
| accuracy on `altered` | 0.968 | 0.903 |
| accuracy on `absent` | 0.852 | **0.963** |
| accuracy on `extended` (the F5 failure mode) | 0.963 | 0.963 |
| cost per claim | $9.5e-5 (Groq list price, measured tokens) | **$1.3e-5** (ratio **7.3×**; gate ≥ 5× ✅) |
| latency per claim | p50 1.59 s | 214 ms (batch 1, T4) |

- **Uncertainty.** A bootstrap over articles (5,000 resamples, because claims of one article are
  correlated) puts the macro-F1 difference at −0.016 [−0.088, +0.049]. The retention ratio
  is [0.91, 1.05].
  - Retention is ≥ 0.95 in 82% of the resamples. The point estimate passes, but 114 claims
    cannot rule out a retention below 0.95.
- **Different mistakes.** The two verifiers disagree on 13 claims. The API is right on 7 of them
  and the local model on 6.
  - The API never says `contradicted` for a supported claim. Its errors are `not_enough_info`
    claims called `contradicted` (4 of 27 `absent`, 1 `extended`).
  - The local model's errors are spread across kinds.

## Cost basis

Local cost is the GPU's hourly price divided by the measured throughput.

- **Throughput:** the best measured value, 4.68 claims/s at batch 1 on the T4.
  - Larger batches were slower (4.42 at batch 8, 3.04 at batch 32) because left-padding of
    variable-length prompts wastes compute.
  - Sorting by length, as F7's ONNX scorer does, is the obvious next step. It is not needed
    for the gate.
- **Price:** $0.22/h, the RunPod community price of the RTX 3090 this project used for F7.
  - RunPod does not list T4s.
  - The 3090 is faster than a T4, so the T4 throughput priced at 3090 rates is an upper bound
    on cost.
- **Break-even:** the cost gate holds up to **$0.32/h** at the T4 throughput. A Google Cloud
  on-demand T4 is about $0.35/h, so at that price the gate would fail narrowly (4.6× instead of 7.3×).
  Spot or community GPUs pass it.
- **Not counted:**
  - An always-on GPU is paid for while idle. The per-claim figure assumes the GPU is busy.
  - At Mizan's current traffic, an idle-priced dedicated GPU costs more than the API calls it
    replaces; see [ADR-0010](../../../docs/adr/0010-local-vs-api-models.md).

## Limits

- **In-distribution advantage.**
  - Train and test claims come from the same generator and the same four constructions. The
    test articles are unseen, but the claim *style* is not.
  - The API model never saw this style.
  - The pre-registered secondary check, agreement with the API on the real claims of the F5
    run (no gold labels), was **not run** and remains open.
- **One run, one seed.**
- **Earlier attempts never scored the test split:**
  - On RunPod, the first attempt ran out of memory (full-sequence logits over a 248k
    vocabulary).
  - The second RunPod attempt reached val macro-F1 0.890 after epoch 1. The pod was then lost
    before it reported anything.
  - On Colab, the first attempt failed to import `peft` (an old `torchao`) for both the main
    and the fallback base.

  No test number from those attempts was ever seen. The fallback base (Qwen2.5-1.5B) was never
  trained.
- **Recovered output.**
  - `verifier_test.jsonl` was rebuilt locally from the Colab cell output: the test claims in
    split order, with the 7 printed misclassifications.
  - The keys, their order and the count (107/114 correct) match the printed file.
  - The 70 MB adapter was not downloaded and is not in the repository.
