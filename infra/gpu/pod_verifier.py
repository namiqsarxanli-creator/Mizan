"""On the pod: train the local verifier, score the test claims once, measure throughput.

bundle/ = src/, articles.json, verify_claims.jsonl
usage: python pod_verifier.py [base]   (default Qwen/Qwen3.5-2B; fallback is pre-registered)
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import torch

from mizan_train.verifier import BASE, Item, LoraConfig, classify, load_base, train_verifier

FALLBACK = "Qwen/Qwen2.5-1.5B-Instruct"  # PREREGISTRATION.md: the only other model allowed

root = Path(__file__).parent
articles = json.loads((root / "articles.json").read_text(encoding="utf-8"))
rows = [json.loads(line) for line in (root / "verify_claims.jsonl").read_text("utf-8").splitlines()]
kept = [r for r in rows if r["kept"]]


def items(split: str) -> list[Item]:
    return [
        Item(r["claim"], articles[r["article"]], r["label"]) for r in kept if r["split"] == split
    ]


train, val, test = items("train"), items("val"), items("test")
print(json.dumps({"train": len(train), "val": len(val), "test": len(test)}))

base = sys.argv[1] if len(sys.argv) > 1 else BASE
try:
    report = train_verifier(train, val, LoraConfig(base=base), root / "runs" / "verifier")
except (torch.OutOfMemoryError, ImportError):
    raise  # memory and missing packages are the environment, not an unsupported architecture
except Exception as exc:  # noqa: BLE001 - any failure of the new architecture triggers the fallback
    print(json.dumps({"base_failed": base, "error": repr(exc)[:500], "fallback": FALLBACK}))
    base = FALLBACK
    report = train_verifier(train, val, LoraConfig(base=base), root / "runs" / "verifier")
print(json.dumps({k: v for k, v in report.items() if k != "history"}))
for h in report["history"]:
    print(json.dumps(h))

# The kept checkpoint, scored once on test.
from peft import PeftModel  # noqa: E402

model, tokenizer = load_base(base, "cuda")
adapter = root / "runs" / "verifier" / "adapter"
if adapter.exists():
    model = PeftModel.from_pretrained(model, str(adapter))
    model = model.merge_and_unload()  # serving form: one dense model, no adapter overhead
preds = classify(model, tokenizer, test)
(root / "verifier_test.jsonl").write_text(
    "".join(
        json.dumps({"key": f"{r['article']}|{r['kind']}", "kind": r["kind"], "label": r["label"],
                    "pred": p}, ensure_ascii=False) + "\n"
        for r, p in zip([r for r in kept if r["split"] == "test"], preds, strict=True)
    ),
    encoding="utf-8",
)  # fmt: skip

# Throughput of the merged model on this GPU, the basis of the cost comparison.
throughput = {}
for batch_size in (1, 8, 32):
    sample = (test * 4)[: max(64, batch_size * 4)]
    classify(model, tokenizer, sample[:batch_size], batch_size=batch_size)  # warm-up
    torch.cuda.synchronize()
    started = time.perf_counter()
    classify(model, tokenizer, sample, batch_size=batch_size)
    torch.cuda.synchronize()
    seconds = time.perf_counter() - started
    throughput[batch_size] = {"claims_per_s": round(len(sample) / seconds, 2),
                              "p_claim_ms": round(1000 * seconds / len(sample), 1)}  # fmt: skip
print(json.dumps({"base": base, "gpu": torch.cuda.get_device_name(0), "throughput": throughput}))
(root / "verifier_throughput.json").write_text(
    json.dumps({"base": base, "gpu": torch.cuda.get_device_name(0), "throughput": throughput})
)
