"""Dense embedding models, run locally on CPU, with a persistent cache.

Embedding the corpus is the slowest step (minutes per model on a laptop CPU), and the corpus
changes rarely: each text is embedded once per model and kept in a SQLite cache keyed by
(model, SHA-256 of the exact input). Re-indexing, comparing chunking strategies or rebuilding
Qdrant then costs nothing.
"""

from __future__ import annotations

import asyncio
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
    # Hugging Face commit to load. The index was built with these exact weights; a pinned
    # revision stops an upstream push from silently changing the vectors (OWASP LLM03).
    revision: str | None = None


MODELS = {
    m.name: m
    for m in (
        ModelSpec(
            "bge-m3", "BAAI/bge-m3", 1024, revision="5617a9f61b028005a4858fdac845db406aefb181"
        ),
        # E5 models are trained with these prefixes and degrade noticeably without them.
        ModelSpec(
            "e5-large",
            "intfloat/multilingual-e5-large",
            1024,
            query_prefix="query: ",
            passage_prefix="passage: ",
            revision="3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3",
        ),
    )
}

# bge-m3 fine-tuned on synthetic Labour Code questions (F7, mizan-train embed). The weights are
# a local artefact, not in git: the model is registered only where they exist, so CI and fresh
# checkouts see the same two public models as before.
FINETUNED = Path(os.environ.get("MIZAN_FINETUNED_EMBEDDINGS", "data/train/runs/bge-m3-mizan"))
if (FINETUNED / "model.safetensors").exists():
    MODELS["bge-m3-mizan"] = ModelSpec("bge-m3-mizan", str(FINETUNED), 1024)


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
                model = SentenceTransformer(
                    self.spec.hf_id, device="cpu", revision=self.spec.revision
                )
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


class QueryBatcher:
    """Embed concurrent queries together (micro-batching).

    One query embedding on CPU costs about 88 ms, and concurrent requests run them one by one
    through a thread pool, contending for the same cores. The API therefore saturated at about
    11 searches a second (evals/results/f11_load). Batched, the same model embeds 19–23 queries
    a second.

    The first query opens a batch. Queries arriving in the next ``max_wait_ms`` join it, up to
    ``max_batch``. One batch is encoded at a time, off the event loop. A lone query waits at
    most ``max_wait_ms`` longer than before; under load, batches grow by themselves.
    """

    def __init__(self, embedder: Embedder, *, max_batch: int = 16, max_wait_ms: float = 10) -> None:
        self._embedder = embedder
        self._max_batch = max_batch
        self._max_wait = max_wait_ms / 1000
        self._queue: asyncio.Queue[tuple[str, asyncio.Future[Vector]]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self.batches = 0  # for tests and metrics

    async def embed_query(self, text: str) -> Vector:
        future: asyncio.Future[Vector] = asyncio.get_running_loop().create_future()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._drain())
        await self._queue.put((text, future))
        return await future

    async def _drain(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            batch = [await self._queue.get()]
            deadline = loop.time() + self._max_wait
            while len(batch) < self._max_batch:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    batch.append(await asyncio.wait_for(self._queue.get(), remaining))
                except TimeoutError:
                    break
            texts = [self._embedder.spec.query_prefix + t for t, _ in batch]
            try:
                vectors = await asyncio.to_thread(self._embedder._encode, texts)
            except Exception as exc:  # noqa: BLE001  every waiter gets the error; the loop survives
                for _, future in batch:
                    if not future.done():
                        future.set_exception(exc)
                continue
            self.batches += 1
            for (_, future), vector in zip(batch, vectors, strict=True):
                if not future.done():
                    future.set_result(vector)

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
