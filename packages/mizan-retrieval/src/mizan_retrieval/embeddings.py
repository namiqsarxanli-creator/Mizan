"""Dense embedding models, run locally on CPU, with a persistent cache.

Embedding the corpus is the slowest step (minutes per model on a laptop CPU), and the corpus
changes rarely: each text is embedded once per model and kept in a SQLite cache keyed by
(model, SHA-256 of the exact input). Re-indexing, comparing chunking strategies or rebuilding
Qdrant then costs nothing.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt

from mizan_core.logging import get_logger

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

Vector = npt.NDArray[np.float32]

log = get_logger(__name__)
_PERSIST_EVERY = 64


@dataclass(frozen=True, slots=True)
class ModelSpec:
    name: str  # short id, also the Qdrant vector name
    hf_id: str
    dim: int
    query_prefix: str = ""
    passage_prefix: str = ""
    max_seq_length: int = 512


MODELS = {
    m.name: m
    for m in (
        ModelSpec("bge-m3", "BAAI/bge-m3", 1024),
        # E5 models are trained with these prefixes and degrade noticeably without them.
        ModelSpec(
            "e5-large",
            "intfloat/multilingual-e5-large",
            1024,
            query_prefix="query: ",
            passage_prefix="passage: ",
        ),
    )
}


class EmbeddingCache:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS embeddings ("
            " model TEXT NOT NULL, sha TEXT NOT NULL, vec BLOB NOT NULL,"
            " PRIMARY KEY (model, sha))"
        )

    @staticmethod
    def key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def get_many(self, model: str, shas: Sequence[str]) -> dict[str, Vector]:
        found: dict[str, Vector] = {}
        with self._lock:
            for i in range(0, len(shas), 500):
                batch = list(shas[i : i + 500])
                marks = ",".join("?" * len(batch))
                rows = self._db.execute(
                    f"SELECT sha, vec FROM embeddings WHERE model = ? AND sha IN ({marks})",  # noqa: S608
                    [model, *batch],
                )
                for sha, blob in rows:
                    found[sha] = np.frombuffer(blob, dtype=np.float32)
        return found

    def put_many(self, model: str, items: dict[str, Vector]) -> None:
        with self._lock, self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO embeddings (model, sha, vec) VALUES (?, ?, ?)",
                [(model, sha, vec.astype(np.float32).tobytes()) for sha, vec in items.items()],
            )

    def close(self) -> None:
        self._db.close()


class Embedder:
    def __init__(self, spec: ModelSpec, cache: EmbeddingCache | None = None, batch_size: int = 8):
        self.spec = spec
        self._cache = cache
        self._batch_size = batch_size
        self._model: SentenceTransformer | None = None
        self._load_lock = threading.Lock()

    def _load(self) -> SentenceTransformer:
        with self._load_lock:
            if self._model is None:
                import torch
                from sentence_transformers import SentenceTransformer

                torch.set_num_threads(os.cpu_count() or 4)
                model = SentenceTransformer(self.spec.hf_id, device="cpu")
                model.max_seq_length = self.spec.max_seq_length
                self._model = model
            return self._model

    def _encode(self, texts: list[str]) -> Vector:
        result: Any = self._load().encode(
            texts,
            batch_size=self._batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=len(texts) > 64,
        )
        return np.asarray(result, dtype=np.float32)

    def embed_passages(self, texts: Sequence[str]) -> Vector:
        inputs = [self.spec.passage_prefix + t for t in texts]
        if self._cache is None:
            return self._encode(inputs)
        shas = [EmbeddingCache.key(t) for t in inputs]
        cached = self._cache.get_many(self.spec.name, shas)
        missing = [i for i, sha in enumerate(shas) if sha not in cached]
        if missing:
            # Longest first: batches of similar length waste less padding on CPU.
            missing.sort(key=lambda i: len(inputs[i]), reverse=True)
            # Persist every slice as soon as it is encoded: a corpus takes many CPU minutes, and
            # an interrupted run must resume where it stopped, not start over.
            for start in range(0, len(missing), _PERSIST_EVERY):
                part = missing[start : start + _PERSIST_EVERY]
                fresh = self._encode([inputs[i] for i in part])
                new = {shas[i]: fresh[j] for j, i in enumerate(part)}
                self._cache.put_many(self.spec.name, new)
                cached.update(new)
                log.info(
                    "embedded",
                    model=self.spec.name,
                    done=min(start + _PERSIST_EVERY, len(missing)),
                    total=len(missing),
                )
        return np.stack([cached[sha] for sha in shas])

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def warmup(self) -> None:
        self._load()

    def embed_query(self, text: str) -> Vector:
        vector: Vector = self._encode([self.spec.query_prefix + text])[0]
        return vector
