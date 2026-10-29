"""A local verifier: a small LLM with a LoRA adapter that labels one claim against one source.

Same task as the production verifier (``mizan_agents.agents.verify``), one claim per request.

- **Training:** supervised fine-tuning on the constructed claims (``verify_data``). The loss
  covers only the answer tokens; the prompt is masked out.
- **Inference:** there is no free-text generation. The model's next-token logits after the
  prompt are compared for the first token of each of the three labels. It is one forward pass,
  it cannot produce an unparseable answer, and it is a calibrated-enough score for argmax.

Model selection uses validation macro-F1; the test split is scored once, at the end.

This module imports nothing from the rest of Mizan beyond ``mizan_core`` logging, so it runs from
the GPU bundle (``infra/gpu``).
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from mizan_core.logging import get_logger

log = get_logger(__name__)

BASE = "Qwen/Qwen3.5-2B"
LABELS = ("supported", "contradicted", "not_enough_info")

SYSTEM = """\
Sən hüquqi iddiaları mənbə ilə yoxlayan yoxlayıcısan. Yalnız verilmiş mənbə mətninə əsasən \
qərar ver:
- supported: mənbə iddianın hamısını birbaşa təsdiqləyir;
- contradicted: mənbə iddianın əksini deyir (başqa müddət, başqa şərt);
- not_enough_info: mənbədən bu nəticə çıxmır, və ya iddiada mənbədə olmayan əlavə tələb var.
Ümumi biliyindən istifadə etmə. Yalnız bir söz yaz: supported, contradicted və ya \
not_enough_info."""


@dataclass(frozen=True, slots=True)
class Item:
    claim: str
    source: str
    label: str = ""  # gold, when known


def messages(item: Item, max_source_chars: int = 2500) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": f"Mənbə:\n{item.source[:max_source_chars]}\n\nİddia: {item.claim}",
        },
    ]


def prompt_text(tokenizer: Any, item: Item) -> str:
    # enable_thinking=False: Qwen3-style templates otherwise open a reasoning block.
    text: str = tokenizer.apply_chat_template(
        messages(item), tokenize=False, add_generation_prompt=True, enable_thinking=False
    )
    return text


def label_token_ids(tokenizer: Any) -> dict[str, int]:
    """The first token of each label; they must differ for argmax over logits to work."""
    ids = {label: tokenizer.encode(label, add_special_tokens=False)[0] for label in LABELS}
    if len(set(ids.values())) != len(LABELS):
        raise ValueError(f"labels share a first token: {ids}")
    return ids


def macro_f1(gold: Sequence[str], pred: Sequence[str]) -> dict[str, Any]:
    per = {}
    for label in LABELS:
        tp = sum(g == p == label for g, p in zip(gold, pred, strict=True))
        fp = sum(p == label and g != label for g, p in zip(gold, pred, strict=True))
        fn = sum(g == label and p != label for g, p in zip(gold, pred, strict=True))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        per[label] = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    accuracy = sum(g == p for g, p in zip(gold, pred, strict=True)) / max(len(gold), 1)
    return {
        "macro_f1": round(sum(per.values()) / len(LABELS), 4),
        "accuracy": round(accuracy, 4),
        "f1": {k: round(v, 4) for k, v in per.items()},
    }


def confusion(gold: Sequence[str], pred: Sequence[str]) -> dict[str, dict[str, int]]:
    out = {g: dict.fromkeys(LABELS, 0) for g in LABELS}
    for g, p in zip(gold, pred, strict=True):
        out[g][p] += 1
    return out


def classify(model: Any, tokenizer: Any, items: Sequence[Item], batch_size: int = 8) -> list[str]:
    import torch

    ids = label_token_ids(tokenizer)
    order = list(ids)
    columns = torch.tensor([ids[label] for label in order])
    tokenizer.padding_side = "left"  # the last position is the next token for every row
    out: list[str] = []
    model.eval()
    with torch.no_grad():
        for start in range(0, len(items), batch_size):
            batch = items[start : start + batch_size]
            enc = tokenizer(
                [prompt_text(tokenizer, i) for i in batch],
                return_tensors="pt",
                padding=True,
                add_special_tokens=False,
            ).to(model.device)
            # Only the last position: full logits are batch x length x 248k-token vocabulary.
            logits = model(**enc, logits_to_keep=1).logits[:, -1, :]
            best = logits[:, columns.to(logits.device)].argmax(dim=-1).tolist()
            out.extend(order[b] for b in best)
    return out


@dataclass(frozen=True, slots=True)
class LoraConfig:
    base: str = BASE
    rank: int = 16
    alpha: int = 32
    dropout: float = 0.05
    lr: float = 2e-4
    epochs: int = 3
    batch_size: int = 8
    max_length: int = 1536
    warmup_ratio: float = 0.05
    seed: int = 13


def load_base(name: str, device: str) -> tuple[Any, Any]:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(name)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    dtype = torch.bfloat16 if device == "cuda" else torch.float32
    model: Any = AutoModelForCausalLM.from_pretrained(name, dtype=dtype)
    return model.to(device), tokenizer


def train_verifier(
    train_items: Sequence[Item], val_items: Sequence[Item], cfg: LoraConfig, out_dir: Path
) -> dict[str, Any]:
    import torch
    from peft import LoraConfig as PeftLora
    from peft import get_peft_model

    random.seed(cfg.seed)
    torch.manual_seed(cfg.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model, tokenizer = load_base(cfg.base, device)
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()
    model = get_peft_model(
        model,
        PeftLora(
            r=cfg.rank,
            lora_alpha=cfg.alpha,
            lora_dropout=cfg.dropout,
            target_modules="all-linear",
            task_type="CAUSAL_LM",
        ),
    )
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)

    def encode(item: Item) -> tuple[list[int], list[int]]:
        prompt = tokenizer.encode(prompt_text(tokenizer, item), add_special_tokens=False)
        answer = [*tokenizer.encode(item.label, add_special_tokens=False), tokenizer.eos_token_id]
        prompt = prompt[-(cfg.max_length - len(answer)) :]
        return prompt + answer, [-100] * len(prompt) + answer

    encoded = [encode(i) for i in train_items]
    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(params, lr=cfg.lr, weight_decay=0.0)
    steps = cfg.epochs * max(len(encoded) // cfg.batch_size, 1)
    warmup = max(int(steps * cfg.warmup_ratio), 1)
    schedule = torch.optim.lr_scheduler.LambdaLR(
        optimizer, lambda s: min((s + 1) / warmup, max(0.0, (steps - s) / (steps - warmup + 1)))
    )

    def evaluate() -> dict[str, Any]:
        pred = classify(model, tokenizer, val_items)
        return macro_f1([i.label for i in val_items], pred)

    history: list[dict[str, Any]] = [{"epoch": 0, "val": evaluate()}]
    log.info("verifier_val", epoch=0, trainable=trainable, **history[0]["val"])
    best = (history[0]["val"]["macro_f1"], 0)
    started = time.perf_counter()
    for epoch in range(1, cfg.epochs + 1):
        model.train()
        order = encoded[:]
        random.shuffle(order)
        losses = []
        for start in range(0, len(order), cfg.batch_size):
            batch = order[start : start + cfg.batch_size]
            # One example per forward pass, gradients accumulated over the batch: no padding,
            # and the answer is always the last tokens, so logits are needed only there.
            # Full-sequence logits over a 248k vocabulary would not fit in 24 GB.
            batch_loss = 0.0
            for ids, labels in batch:
                answer = sum(label != -100 for label in labels)
                out = model(input_ids=torch.tensor([ids], device=device), logits_to_keep=answer + 1)
                # Position t predicts token t+1: the kept logits minus the last predict the
                # answer tokens.
                logits = out.logits[0, :-1].float()
                target = torch.tensor(ids[-answer:], device=device)
                loss = torch.nn.functional.cross_entropy(logits, target) / len(batch)
                loss.backward()  # type: ignore[no-untyped-call]
                batch_loss += loss.item()
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            optimizer.step()
            schedule.step()
            optimizer.zero_grad()
            losses.append(batch_loss)
        val = evaluate()
        history.append({"epoch": epoch, "loss": sum(losses) / len(losses), "val": val})
        log.info("verifier_epoch", epoch=epoch, loss=history[-1]["loss"], **val)
        if val["macro_f1"] > best[0]:
            best = (val["macro_f1"], epoch)
            model.save_pretrained(str(out_dir / "adapter"))
            tokenizer.save_pretrained(str(out_dir / "adapter"))
    report = {
        "config": asdict(cfg),
        "train_claims": len(train_items),
        "val_claims": len(val_items),
        "trainable_params": trainable,
        "device": device,
        "best_epoch": best[1],
        "minutes": round((time.perf_counter() - started) / 60, 1),
        "history": history,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report
