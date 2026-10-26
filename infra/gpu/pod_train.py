"""Runs on the RunPod pod: fine-tune bge-m3 from a self-contained bundle.

bundle/ = src/ (mizan_core, mizan_ingestion, mizan_train), articles.json, examples.jsonl
usage: python pod_train.py NAME [layers] [lr] [epochs] [batch]
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from mizan_train.dataset import read_examples
from mizan_train.embed import TrainConfig, train

root = Path(__file__).parent
name = sys.argv[1]
layers, lr, epochs, batch = (sys.argv[2:] + ["0", "2e-5", "3", "32"][len(sys.argv) - 2 :])[:4]
cfg = TrainConfig(
    trainable_layers=int(layers),
    lr=float(lr),
    epochs=int(epochs),
    batch_size=int(batch),
    gradient_checkpointing=True,
)
articles = json.loads((root / "articles.json").read_text(encoding="utf-8"))
report = train(read_examples(root / "examples.jsonl"), articles, cfg, root / "runs" / name)
print(json.dumps({k: v for k, v in report.items() if k != "history"}, indent=1))
for h in report["history"]:
    print(json.dumps(h))
