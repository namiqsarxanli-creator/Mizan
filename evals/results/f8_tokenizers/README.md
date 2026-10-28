# F8: tokenizer efficiency on Azerbaijani, and the base model choice

`mizan-train tokenizers` encodes two corpora with each candidate's tokenizer:

- **law:** the Labour Code in force, 331 articles, 438k characters;
- **questions:** the 351 eval questions written by people, 23k characters.

Full numbers are in `report.json`.

| tokenizer | vocab | fertility, law (tokens/word) | fertility, questions | chars/token | words in ≥3 pieces | tokens vs Qwen2.5 |
|---|---|---|---|---|---|---|
| `Qwen/Qwen2.5-1.5B-Instruct` | 151,665 | 4.05 | 3.83 | 1.96 | 73% | 1.00 |
| `Qwen/Qwen3-1.7B` | 151,669 | 4.05 | 3.83 | 1.96 | 73% | 1.00 |
| **`Qwen/Qwen3.5-2B` / `-4B`** | 248,077 | **3.14** | **2.98** | 2.52 | 62% | **0.78** |
| `unsloth/Llama-3.2-1B-Instruct` | 128,256 | 3.90 | 3.71 | 2.03 | 70% | 0.96 |
| `unsloth/gemma-3-1b-it` / `3n-E2B` | 262,145 | 3.17 | 3.08 | 2.50 | 63% | 0.78 |
| *`openai/gpt-oss-20b` (serving LLM)* | 200,019 | 2.46 | 2.49 | 3.22 | 57% | 0.61 |
| *`BAAI/bge-m3` (embeddings)* | 250,002 | 1.70 | 1.80 | 4.67 | 10% | 0.42 |

## Reading

- **Azerbaijani is expensive for every small open model.** An Azerbaijani word costs 3–4 tokens,
  where English costs about 1.3. "əmək məzuniyyəti" (labour leave) is 11 tokens for Llama 3.2,
  and `ə` is its own byte-level token. The language is agglutinative, and these vocabularies saw
  little of it.
- **The 248k-vocabulary generation helps a lot.** Qwen3.5 and Gemma 3 need 22% fewer tokens than
  Qwen2.5/Qwen3 for the same text. The saving applies to the context window, to latency and to
  training compute alike.
- The serving LLM's tokenizer (o200k) and the embedding model's tokenizer (XLM-R) are better
  still. They are references, not candidates for a 2B model.

## Decision: `Qwen/Qwen3.5-2B`

- **Efficiency:** best measured, tied with Gemma 3.
- **Licence:** Apache-2.0. Gemma's terms add use restrictions that a published adapter would
  inherit.
- **Size:** 2B parameters, 4.5 GB in bf16. LoRA in bf16 fits a 24 GB card without 4-bit
  quantisation, and QLoRA remains an option for larger batches.
- **Risk:** the model is new, a hybrid multimodal architecture (`Qwen3_5ForConditionalGeneration`).
  If the training or serving stack does not support it, the fallback is `Qwen2.5-1.5B-Instruct`.
  That fallback is mature tooling at 29% more tokens per text, and the switch would be recorded
  here.
