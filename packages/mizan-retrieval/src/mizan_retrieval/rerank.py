"""Cross-encoder reranking.

Bi-encoders (and BM25) score query and passage independently; a cross-encoder reads them
together and is much better at "does this passage answer this question", at a cost that only
allows it on a short candidate list.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder

DEFAULT_RERANKER = "BAAI/bge-reranker-v2-m3"


class Reranker:
    def __init__(self, hf_id: str = DEFAULT_RERANKER, max_length: int = 512, batch_size: int = 8):
        self.hf_id = hf_id
        self._max_length = max_length
        self._batch_size = batch_size
        self._model: CrossEncoder | None = None
        self._lock = threading.Lock()

    def _load(self) -> CrossEncoder:
        with self._lock:
            if self._model is None:
                import torch
                from sentence_transformers import CrossEncoder

                torch.set_num_threads(os.cpu_count() or 4)
                self._model = CrossEncoder(self.hf_id, max_length=self._max_length, device="cpu")
            return self._model

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def warmup(self) -> None:
        self._load()

    def scores(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        result: Any = self._load().predict(
            [(query, p) for p in passages], batch_size=self._batch_size, show_progress_bar=False
        )
        return [float(s) for s in result]
