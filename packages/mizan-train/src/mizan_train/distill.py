"""Distil the cross-encoder reranker into a small student (F7 week 17).

The teacher, bge-reranker-v2-m3 (568M), lifts MRR@10 on the eval sets but costs ~17 s per
query on the serving CPU, so it is off in production. The student is a 118M multilingual
MiniLM cross-encoder trained to reproduce the teacher's *ranking* of the same candidates.

- **Candidates.** For every question, the dense retriever's top-k articles: exactly the list
  the reranker sees in serving. Teacher and student are compared on identical lists, so the
  only difference between them is the scorer.
- **Loss.** Listwise KL divergence between the teacher's and the student's softmax over a
  question's candidates (temperature ``T``). Only the order matters at serving time, and the
  listwise loss ignores the teacher's absolute scale, which a pointwise MSE would have to fit.
- **Labels.** Training questions are synthetic (``mizan-train synth``); the student never sees
  gold labels, only teacher scores, so the eval sets' human questions stay untouched.

Model selection uses validation MRR@10 against the gold article; test and the eval sets are
read once, at the end.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from mizan_core.logging import get_logger

log = get_logger(__name__)

TEACHER = "BAAI/bge-reranker-v2-m3"
STUDENT = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"


@dataclass(slots=True)
class Candidates:
    id: str
    question: str
    split: str  # train / val / test, or eval:<dataset>
    gold: list[str]
    articles: list[str]  # dense top-k article keys, in retriever order
    passages: list[str]  # the matching text keys ("article|valid_from"): the version in force
    teacher: list[float] = field(default_factory=list)


def write_candidates(path: Path, rows: Iterable[Candidates]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")


def read_candidates(path: Path) -> list[Candidates]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [Candidates(**json.loads(line)) for line in lines if line]


def per_question(
    rows: Sequence[Candidates], scores: Sequence[Sequence[float]]
) -> list[dict[str, float]]:
    """Hit@1, Recall@5 and MRR@10 of each question after sorting its list by ``scores``."""
    out = []
    for row, s in zip(rows, scores, strict=True):
        order = [row.articles[i] for i in np.argsort(-np.asarray(s), kind="stable")]
        gold = set(row.gold)
        first = next((i for i, a in enumerate(order[:10], 1) if a in gold), 0)
        out.append(
            {
                "Hit@1": float(order[0] in gold),
                "Recall@5": len(gold.intersection(order[:5])) / len(gold),
                "MRR@10": 1 / first if first else 0.0,
            }
        )
    return out


def rank_metrics(rows: Sequence[Candidates], scores: Sequence[Sequence[float]]) -> dict[str, float]:
    """``per_question`` averaged over the questions."""
    each = per_question(rows, scores)
    return {m: float(np.mean([q[m] for q in each])) for m in ("Hit@1", "Recall@5", "MRR@10")}


def retriever_order(rows: Sequence[Candidates]) -> list[list[float]]:
    """Scores that keep the dense retriever's order: the no-rerank baseline."""
    return [[-float(i) for i in range(len(r.articles))] for r in rows]


def score(
    model: Any, rows: Sequence[Candidates], texts: dict[str, str], batch_size: int = 64
) -> list[list[float]]:
    """Cross-encoder scores for every (question, candidate) pair, grouped per question."""
    pairs = [(r.question, texts[p]) for r in rows for p in r.passages]
    flat = model.predict(pairs, batch_size=batch_size, show_progress_bar=False)
    out, i = [], 0
    for r in rows:
        out.append([float(x) for x in flat[i : i + len(r.articles)]])
        i += len(r.articles)
    return out


@dataclass(frozen=True, slots=True)
class DistillConfig:
    student: str = STUDENT
    lr: float = 3e-5
    epochs: int = 3
    questions_per_batch: int = 8  # x candidates pairs per step
    temperature: float = 1.0
    max_length: int = 512
    warmup_ratio: float = 0.1
    seed: int = 13


def train_student(
    rows: Sequence[Candidates], texts: dict[str, str], cfg: DistillConfig, out_dir: Path
) -> dict[str, Any]:
    import torch
    from sentence_transformers import CrossEncoder

    random.seed(cfg.seed)
    torch.manual_seed(cfg.seed)
    train_set = [r for r in rows if r.split == "train" and r.teacher]
    val_set = [r for r in rows if r.split == "val"]
    device = "cuda" if torch.cuda.is_available() else "cpu"
    student = CrossEncoder(cfg.student, max_length=cfg.max_length, device=device)
    model, tokenizer = student.model, student.tokenizer
    optimizer = torch.optim.AdamW(model.parameters(), lr=cfg.lr, weight_decay=0.01)
    steps = cfg.epochs * max(len(train_set) // cfg.questions_per_batch, 1)
    warmup = max(int(steps * cfg.warmup_ratio), 1)
    schedule = torch.optim.lr_scheduler.LambdaLR(
        optimizer, lambda s: min((s + 1) / warmup, max(0.0, (steps - s) / (steps - warmup + 1)))
    )
    autocast = torch.autocast("cuda", dtype=torch.bfloat16, enabled=device == "cuda")

    history: list[dict[str, Any]] = []
    val = rank_metrics(val_set, score(student, val_set, texts))
    history.append({"epoch": 0, "val": val})
    log.info("distill_val", epoch=0, device=device, **val)
    best = (val["MRR@10"], 0)
    started = time.perf_counter()
    for epoch in range(1, cfg.epochs + 1):
        model.train()
        order = train_set[:]
        random.shuffle(order)
        losses = []
        for start in range(0, len(order), cfg.questions_per_batch):
            batch = order[start : start + cfg.questions_per_batch]
            pairs = [(r.question, texts[p]) for r in batch for p in r.passages]
            features = tokenizer(
                [q for q, _ in pairs],
                [p for _, p in pairs],
                truncation="only_second",
                max_length=cfg.max_length,
                padding=True,
                return_tensors="pt",
            ).to(device)
            with autocast:
                logits = model(**features).logits.squeeze(-1).float()
            loss = torch.zeros((), device=device)
            i = 0
            for r in batch:
                n = len(r.articles)
                target = torch.tensor(r.teacher, device=device) / cfg.temperature
                student_log = torch.log_softmax(logits[i : i + n] / cfg.temperature, dim=-1)
                loss = loss + torch.nn.functional.kl_div(
                    student_log, torch.log_softmax(target, dim=-1), log_target=True,
                    reduction="sum",
                )  # fmt: skip
                i += n
            loss = loss / len(batch)
            loss.backward()  # type: ignore[no-untyped-call]
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            schedule.step()
            optimizer.zero_grad()
            losses.append(float(loss))
        model.eval()
        val = rank_metrics(val_set, score(student, val_set, texts))
        history.append({"epoch": epoch, "loss": float(np.mean(losses)), "val": val})
        log.info("distill_epoch", epoch=epoch, loss=float(np.mean(losses)), **val)
        if val["MRR@10"] > best[0]:
            best = (val["MRR@10"], epoch)
            student.save(str(out_dir / "model"))
    report = {
        "config": asdict(cfg),
        "train_questions": len(train_set),
        "val_questions": len(val_set),
        "device": device,
        "best_epoch": best[1],
        "minutes": round((time.perf_counter() - started) / 60, 1),
        "history": history,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report
