"""On the pod: teacher scores, student distillation, and scores of every system on every row.

bundle/ = src/, rerank_candidates.jsonl, article_versions.json
usage: python pod_distill.py [lr ...]   (one student run per learning rate, in order)
"""

import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import torch
from sentence_transformers import CrossEncoder

from mizan_train.distill import (
    STUDENT,
    TEACHER,
    DistillConfig,
    read_candidates,
    score,
    train_student,
    write_candidates,
)

root = Path(__file__).parent
rows = read_candidates(root / "rerank_candidates.jsonl")
texts = json.loads((root / "article_versions.json").read_text(encoding="utf-8"))

scored = root / "scored_candidates.jsonl"
if scored.exists():
    rows = read_candidates(scored)
else:
    teacher = CrossEncoder(TEACHER, max_length=512, device="cuda")
    teacher.model.half()
    for r, s in zip(rows, score(teacher, rows, texts), strict=True):
        r.teacher = s
    write_candidates(scored, rows)
    del teacher
    torch.cuda.empty_cache()

runs = {}
for lr in [float(x) for x in sys.argv[1:]] or [3e-5]:
    name = f"student-lr{lr:g}"
    report = train_student(rows, texts, DistillConfig(lr=lr), root / "runs" / name)
    runs[name] = report
    print(json.dumps({name: {k: v for k, v in report.items() if k != "history"}}))
    for h in report["history"]:
        print(json.dumps(h))

# The kept run: best val MRR@10 (pre-registered); score every row with it for the report.
best = max(runs, key=lambda n: max(h["val"]["MRR@10"] for h in runs[n]["history"]))
kept = root / "runs" / best / "model"
# No epoch beat the untrained student on val: then the untrained student is what is kept.
student = CrossEncoder(str(kept) if kept.exists() else STUDENT, max_length=512, device="cuda")
out = [asdict(r) | {"student": s} for r, s in zip(rows, score(student, rows, texts), strict=True)]
(root / "all_scores.jsonl").write_text(
    "".join(json.dumps(o, ensure_ascii=False) + "\n" for o in out), encoding="utf-8"
)
print(json.dumps({"kept": best}))
