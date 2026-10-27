"""Export a cross-encoder to ONNX and quantise it to int8 for CPU serving (F7 week 17).

Dynamic quantisation stores the linear layers' weights as int8 and quantises activations on
the fly: no calibration data, about 4x smaller weights, and on CPU the int8 matrix
multiplications are usually 1.5-3x faster than fp32. Whether ranking quality survives is an
empirical question, answered on the eval sets, not assumed.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np


def export(model_dir: Path, out_dir: Path, *, opset: int = 17) -> dict[str, Path]:
    """``model.onnx`` (fp32) and ``model.int8.onnx`` next to the tokenizer files."""
    import torch
    from onnxruntime.quantization import QuantType, quantize_dynamic
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    out_dir.mkdir(parents=True, exist_ok=True)
    tokenizer = AutoTokenizer.from_pretrained(model_dir)
    model = AutoModelForSequenceClassification.from_pretrained(model_dir).eval()
    sample = tokenizer(["sual"], ["mətn"], return_tensors="pt")
    names = [n for n in ("input_ids", "attention_mask", "token_type_ids") if n in sample]
    fp32 = out_dir / "model.onnx"
    with torch.no_grad():
        torch.onnx.export(
            model,
            tuple(sample[n] for n in names),
            str(fp32),
            input_names=names,
            output_names=["logits"],
            dynamic_axes={n: {0: "batch", 1: "seq"} for n in names} | {"logits": {0: "batch"}},
            opset_version=opset,
            dynamo=False,
        )
    int8 = out_dir / "model.int8.onnx"
    quantize_dynamic(str(fp32), str(int8), weight_type=QuantType.QInt8)
    tokenizer.save_pretrained(out_dir)
    for name in ("config.json",):
        if (model_dir / name).exists():
            shutil.copy(model_dir / name, out_dir / name)
    return {"fp32": fp32, "int8": int8}


class OnnxCrossEncoder:
    """``CrossEncoder.predict`` over an ONNX Runtime session (CPU)."""

    def __init__(self, model_dir: Path, file: str = "model.int8.onnx", max_length: int = 512):
        import onnxruntime as ort
        from transformers import AutoTokenizer

        options = ort.SessionOptions()
        # Physical cores, not logical ones: with all hyperthreads ORT ran ~3x slower here.
        options.intra_op_num_threads = max(1, (os.cpu_count() or 8) // 2)
        self._session = ort.InferenceSession(
            str(model_dir / file), options, providers=["CPUExecutionProvider"]
        )
        self._inputs = {i.name for i in self._session.get_inputs()}
        self._tokenizer = AutoTokenizer.from_pretrained(model_dir)
        self._max_length = max_length

    def predict(
        self, pairs: Sequence[tuple[str, str]], batch_size: int = 16, **_: Any
    ) -> list[float]:
        # Longest first, as sentence-transformers does: similar lengths in a batch waste less
        # padding, which dominates the cost of 512-token legal passages.
        order = sorted(range(len(pairs)), key=lambda i: -len(pairs[i][0]) - len(pairs[i][1]))
        scores = [0.0] * len(pairs)
        for start in range(0, len(order), batch_size):
            idx = order[start : start + batch_size]
            batch = [pairs[i] for i in idx]
            enc = self._tokenizer(
                [q for q, _ in batch],
                [p for _, p in batch],
                truncation="only_second",
                max_length=self._max_length,
                padding=True,
                return_tensors="np",
            )
            feeds = {k: v.astype(np.int64) for k, v in enc.items() if k in self._inputs}
            logits = self._session.run(["logits"], feeds)[0]
            for i, x in zip(idx, np.asarray(logits).reshape(len(batch), -1)[:, 0], strict=True):
                scores[i] = float(x)
        return scores
