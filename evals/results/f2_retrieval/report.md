# Retrieval evaluation

- Dataset: `evals/datasets/v0/retrieval.jsonl` (70 questions: 15 multi, 51 single, 4 temporal)
- Generated: 2026-10-16 16:54 UTC
- Metrics at article level; `mean [95% bootstrap CI]`; latency is p50 on CPU.

| Variant | Hit@1 | Hit@5 | Recall@5 | MRR@10 | nDCG@10 | p50 ms |
|---|---|---|---|---|---|---|
| bm25 (article chunks) | 0.286 [0.19, 0.39] | 0.571 [0.46, 0.69] | 0.550 [0.44, 0.66] | 0.404 [0.31, 0.50] | 0.456 [0.36, 0.55] | 272 |
| bm25_p5 (article chunks) | 0.386 [0.27, 0.49] | 0.629 [0.51, 0.74] | 0.593 [0.48, 0.70] | 0.495 [0.40, 0.59] | 0.539 [0.45, 0.63] | 270 |
| bm25_p6 (article chunks) | 0.371 [0.26, 0.49] | 0.614 [0.50, 0.73] | 0.586 [0.48, 0.69] | 0.464 [0.36, 0.57] | 0.509 [0.42, 0.60] | 270 |
| bm25_p7 (article chunks) | 0.343 [0.23, 0.46] | 0.600 [0.49, 0.71] | 0.579 [0.47, 0.69] | 0.457 [0.36, 0.56] | 0.503 [0.41, 0.60] | 271 |
| bge-m3 (article chunks) | 0.743 [0.64, 0.84] | 0.957 [0.90, 1.00] | 0.929 [0.87, 0.98] | 0.831 [0.76, 0.90] | 0.848 [0.79, 0.90] | 376 |
| e5-large (article chunks) | 0.586 [0.47, 0.70] | 0.843 [0.76, 0.93] | 0.790 [0.70, 0.88] | 0.704 [0.62, 0.79] | 0.734 [0.66, 0.81] | 379 |
| bge-m3 (part chunks) | 0.729 [0.63, 0.83] | 0.929 [0.86, 0.99] | 0.905 [0.84, 0.96] | 0.818 [0.74, 0.89] | 0.846 [0.79, 0.90] | 372 |
| bm25_p5 (part chunks) | 0.386 [0.27, 0.50] | 0.600 [0.49, 0.71] | 0.557 [0.44, 0.66] | 0.480 [0.38, 0.58] | 0.523 [0.43, 0.61] | 273 |
| bge-m3 + bm25_p5 (article chunks) | 0.600 [0.49, 0.71] | 0.943 [0.89, 0.99] | 0.895 [0.83, 0.95] | 0.741 [0.67, 0.82] | 0.776 [0.72, 0.84] | 389 |
| e5-large + bm25_p5 (article chunks) | 0.557 [0.44, 0.67] | 0.786 [0.69, 0.87] | 0.740 [0.64, 0.83] | 0.662 [0.57, 0.76] | 0.693 [0.61, 0.78] | 380 |
| bge-m3 + bm25_p5 (part chunks) | 0.543 [0.43, 0.66] | 0.914 [0.84, 0.97] | 0.886 [0.81, 0.95] | 0.705 [0.63, 0.79] | 0.752 [0.69, 0.82] | 364 |
| bge-m3 + bm25_p5 + rerank@20 (article chunks) | 0.771 [0.67, 0.87] | 0.957 [0.91, 1.00] | 0.910 [0.85, 0.96] | 0.853 [0.79, 0.92] | 0.856 [0.80, 0.91] | 18321 |
| bge-m3 + bm25_p5 + rerank@20 (part chunks) | 0.771 [0.67, 0.87] | 0.943 [0.89, 0.99] | 0.902 [0.84, 0.96] | 0.844 [0.77, 0.91] | 0.850 [0.79, 0.91] | 11164 |
| bm25_p5 + rewrite (article chunks) | 0.400 [0.29, 0.51] | 0.686 [0.57, 0.79] | 0.657 [0.55, 0.76] | 0.520 [0.42, 0.62] | 0.559 [0.47, 0.64] | 289 |
| bge-m3 + bm25_p5 + rewrite (article chunks) | 0.657 [0.54, 0.77] | 0.886 [0.80, 0.96] | 0.860 [0.78, 0.93] | 0.755 [0.67, 0.84] | 0.779 [0.70, 0.85] | 485 |
| bge-m3 + rerank@20 (article chunks) | 0.786 [0.69, 0.89] | 0.957 [0.91, 1.00] | 0.910 [0.85, 0.96] | 0.863 [0.80, 0.93] | 0.862 [0.81, 0.91] | 17167 |
| bge-m3 + rewrite (article chunks) | 0.700 [0.59, 0.80] | 0.971 [0.93, 1.00] | 0.914 [0.86, 0.96] | 0.813 [0.74, 0.88] | 0.825 [0.76, 0.88] | 398 |

## Recall@5 by question type

| Variant | multi | single | temporal |
|---|---|---|---|
| bm25 (article chunks) | 0.37 | 0.59 | 0.75 |
| bm25_p5 (article chunks) | 0.37 | 0.65 | 0.75 |
| bm25_p6 (article chunks) | 0.40 | 0.65 | 0.50 |
| bm25_p7 (article chunks) | 0.43 | 0.61 | 0.75 |
| bge-m3 (article chunks) | 0.80 | 0.98 | 0.75 |
| e5-large (article chunks) | 0.56 | 0.84 | 1.00 |
| bge-m3 (part chunks) | 0.82 | 0.94 | 0.75 |
| bm25_p5 (part chunks) | 0.40 | 0.59 | 0.75 |
| bge-m3 + bm25_p5 (article chunks) | 0.71 | 0.96 | 0.75 |
| e5-large + bm25_p5 (article chunks) | 0.52 | 0.78 | 1.00 |
| bge-m3 + bm25_p5 (part chunks) | 0.73 | 0.94 | 0.75 |
| bge-m3 + bm25_p5 + rerank@20 (article chunks) | 0.71 | 0.96 | 1.00 |
| bge-m3 + bm25_p5 + rerank@20 (part chunks) | 0.68 | 0.96 | 1.00 |
| bm25_p5 + rewrite (article chunks) | 0.40 | 0.73 | 0.75 |
| bge-m3 + bm25_p5 + rewrite (article chunks) | 0.68 | 0.92 | 0.75 |
| bge-m3 + rerank@20 (article chunks) | 0.71 | 0.96 | 1.00 |
| bge-m3 + rewrite (article chunks) | 0.73 | 0.96 | 1.00 |

## Misses of the best variant (bge-m3 (article chunks)): Hit@5 = 0

- `q002` Müdir məni heç bir səbəb göstərmədən işdən çıxara bilərmi? → expected art:68, art:70
- `q014` Uzun illər eyni yerdə işləyənə əlavə istirahət günləri düşürmü? → expected art:116
- `q068` İşdən çıxmaq üçün işəgötürəni nə qədər əvvəl xəbərdar etməliyəm? → expected art:69
