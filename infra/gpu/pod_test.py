"""On the pod: test-split metrics for base vs the selected run, then an fp16 copy to download."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import torch
from sentence_transformers import SentenceTransformer

from mizan_train.dataset import read_examples
from mizan_train.embed import evaluate

root = Path(__file__).parent
run = sys.argv[1]
articles = json.loads((root / "articles.json").read_text(encoding="utf-8"))
test = [
    e
    for e in read_examples(root / "examples.jsonl")
    if e.split == "test" and e.positive in articles
]
out = {"test_examples": len(test)}
for label, path in [("base", "BAAI/bge-m3"), ("tuned", str(root / "runs" / run / "model"))]:
    model = SentenceTransformer(path, device="cuda")
    model.max_seq_length = 384
    out[label] = evaluate(model, test, articles)
    if label == "tuned":
        model.half().save(str(root / "export" / "bge-m3-mizan"))
    del model
    torch.cuda.empty_cache()
print(json.dumps(out, indent=1))
(root / "runs" / run / "test.json").write_text(json.dumps(out, indent=1))
