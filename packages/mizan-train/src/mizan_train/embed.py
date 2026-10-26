"""Fine-tune the embedding model on (question, article) pairs (F7 week 16).

Loss: multiple-negatives ranking (InfoNCE). Each question is scored against every passage in
the batch (its own article, the other questions' articles, and mined hard negatives), and
cross-entropy pushes its own article to the top. Two details matter for legal text:

- **Same-article masking.** Several questions in a batch can share an article; their articles
  are not negatives for each other, so those scores are masked rather than penalised.
- **Partial fine-tuning.** On a CPU with 15 GB of RAM, training all 568M parameters of bge-m3
  with AdamW does not fit. Only the top ``trainable_layers`` transformer layers are trained:
  the lower layers keep the multilingual model's general knowledge, and the top layers adapt
  to the domain. Frozen layers store no activations for backward, so memory and time drop
  roughly in proportion.

Model selection uses the validation split (articles never seen in training): the epoch with
the best validation MRR is kept. The test split and the evaluation datasets are not touched
here.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from mizan_core.logging import get_logger
from mizan_train.dataset import Example

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TrainConfig:
    base: str = "BAAI/bge-m3"
    trainable_layers: int = 6  # 0 = all layers (full fine-tuning, for a GPU)
    lr: float = 2e-5
    epochs: int = 3
    batch_size: int = 16
    hard_negatives: int = 1  # per question, from the mined list
    max_seq_length: int = 384
    scale: float = 20.0  # cosine similarity temperature (1 / 0.05)
    warmup_ratio: float = 0.1
    seed: int = 13
    device: str = "auto"  # "auto" -> CUDA when available, else CPU
    gradient_checkpointing: bool = False  # trade compute for memory on smaller GPUs


def freeze_lower_layers(model: Any, trainable_layers: int) -> int:
    """Freeze embeddings and all but the top ``trainable_layers``; return trainable params.

    ``trainable_layers <= 0`` trains everything (full fine-tuning).
    """
    transformer = model[0].auto_model
    layers = transformer.encoder.layer
    if trainable_layers <= 0 or trainable_layers >= len(layers):
        for p in transformer.parameters():
            p.requires_grad = True
        return sum(p.numel() for p in model.parameters() if p.requires_grad)
    for p in transformer.parameters():
        p.requires_grad = False
    for layer in layers[len(layers) - trainable_layers :]:
        for p in layer.parameters():
            p.requires_grad = True
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


def ranking_metrics(
    query_vecs: np.ndarray,
    passage_vecs: np.ndarray,
    gold: Sequence[int],
    ks: Sequence[int] = (1, 5),
) -> dict[str, float]:
    """Recall@k and MRR@10 of each query's gold passage among all passages (cosine)."""
    scores = query_vecs @ passage_vecs.T
    ranks = []
    for i, g in enumerate(gold):
        ranks.append(int((scores[i] > scores[i, g]).sum()) + 1)
    out = {f"R@{k}": float(np.mean([r <= k for r in ranks])) for k in ks}
    out["MRR@10"] = float(np.mean([1 / r if r <= 10 else 0.0 for r in ranks]))
    return out


def evaluate(
    model: Any, examples: Sequence[Example], articles: dict[str, str], batch_size: int = 16
) -> dict[str, float]:
    keys = sorted(articles)
    index = {k: i for i, k in enumerate(keys)}
    passages = model.encode(
        [articles[k] for k in keys], batch_size=batch_size, normalize_embeddings=True
    )
    queries = model.encode(
        [e.question for e in examples], batch_size=batch_size, normalize_embeddings=True
    )
    return ranking_metrics(queries, passages, [index[e.positive] for e in examples])


def train(
    examples: Sequence[Example],
    articles: dict[str, str],
    cfg: TrainConfig,
    out_dir: Path,
) -> dict[str, Any]:
    import torch
    from sentence_transformers import SentenceTransformer

    random.seed(cfg.seed)
    torch.manual_seed(cfg.seed)
    train_set = [e for e in examples if e.split == "train" and e.positive in articles]
    val_set = [e for e in examples if e.split == "val" and e.positive in articles]

    device = (
        cfg.device if cfg.device != "auto" else ("cuda" if torch.cuda.is_available() else "cpu")
    )
    model = SentenceTransformer(cfg.base, device=device)
    model.max_seq_length = cfg.max_seq_length
    if cfg.gradient_checkpointing:
        transformer: Any = model[0].auto_model
        transformer.gradient_checkpointing_enable()
    # bf16 autocast on GPU: half the activation memory, no loss scaling needed (unlike fp16).
    autocast = torch.autocast("cuda", dtype=torch.bfloat16, enabled=device == "cuda")
    trainable = freeze_lower_layers(model, cfg.trainable_layers)
    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(params, lr=cfg.lr, weight_decay=0.01)
    steps = cfg.epochs * max(len(train_set) // cfg.batch_size, 1)
    warmup = max(int(steps * cfg.warmup_ratio), 1)
    schedule = torch.optim.lr_scheduler.LambdaLR(
        optimizer, lambda s: min((s + 1) / warmup, max(0.0, (steps - s) / (steps - warmup + 1)))
    )

    def embed(texts: list[str]) -> torch.Tensor:
        features = {
            k: v.to(device) if torch.is_tensor(v) else v for k, v in model.tokenize(texts).items()
        }
        with autocast:
            out = model(features)["sentence_embedding"]
        return torch.nn.functional.normalize(out.float(), dim=-1)

    history: list[dict[str, Any]] = []
    base_val = evaluate(model, val_set, articles)
    history.append({"epoch": 0, "val": base_val})
    log.info("embed_val", epoch=0, trainable=trainable, device=device, **base_val)
    best = (base_val["MRR@10"], 0)
    started = time.perf_counter()
    for epoch in range(1, cfg.epochs + 1):
        model.train()
        order = train_set[:]
        random.shuffle(order)
        losses = []
        for start in range(0, len(order) - cfg.batch_size + 1, cfg.batch_size):
            batch = order[start : start + cfg.batch_size]
            passage_keys = [e.positive for e in batch]
            for e in batch:
                passage_keys += [n for n in e.negatives[: cfg.hard_negatives] if n in articles]
            q = embed([e.question for e in batch])
            p = embed([articles[k] for k in passage_keys])
            scores = q @ p.T * cfg.scale
            # Another question's copy of my article is not a negative for me.
            mask = torch.tensor(
                [[k == e.positive and j != i for j, k in enumerate(passage_keys)]
                 for i, e in enumerate(batch)],
                device=device,
            )  # fmt: skip
            scores = scores.masked_fill(mask, float("-inf"))
            loss = torch.nn.functional.cross_entropy(
                scores, torch.arange(len(batch), device=device)
            )
            loss.backward()  # type: ignore[no-untyped-call]
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            optimizer.step()
            schedule.step()
            optimizer.zero_grad()
            losses.append(float(loss))
        model.eval()
        val = evaluate(model, val_set, articles)
        history.append({"epoch": epoch, "loss": float(np.mean(losses)), "val": val})
        log.info("embed_epoch", epoch=epoch, loss=float(np.mean(losses)), **val)
        if val["MRR@10"] > best[0]:
            best = (val["MRR@10"], epoch)
            model.save(str(out_dir / "model"))
    report = {
        "config": asdict(cfg),
        "train_examples": len(train_set),
        "val_examples": len(val_set),
        "trainable_params": trainable,
        "device": device,
        "best_epoch": best[1],
        "minutes": round((time.perf_counter() - started) / 60, 1),
        "history": history,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report
