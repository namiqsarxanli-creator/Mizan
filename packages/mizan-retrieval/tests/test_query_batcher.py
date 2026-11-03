import asyncio
from typing import Any

import numpy as np
import pytest

from mizan_retrieval.embeddings import QueryBatcher


class FakeEmbedder:
    def __init__(self, fail: bool = False) -> None:
        self.spec = type("Spec", (), {"query_prefix": "q: "})()
        self.calls: list[list[str]] = []
        self.fail = fail

    def _encode(self, texts: list[str]) -> Any:
        self.calls.append(texts)
        if self.fail:
            raise RuntimeError("model failed")
        return np.array([[float(len(t))] for t in texts], dtype=np.float32)


async def test_concurrent_queries_share_one_encode_and_keep_their_order() -> None:
    embedder = FakeEmbedder()
    batcher = QueryBatcher(embedder, max_wait_ms=50)  # type: ignore[arg-type]
    vectors = await asyncio.gather(*(batcher.embed_query("x" * n) for n in (1, 2, 3)))
    assert embedder.calls == [["q: x", "q: xx", "q: xxx"]]
    assert [float(v[0]) for v in vectors] == [4.0, 5.0, 6.0]
    await batcher.close()


async def test_batches_are_capped() -> None:
    embedder = FakeEmbedder()
    batcher = QueryBatcher(embedder, max_batch=2, max_wait_ms=50)  # type: ignore[arg-type]
    await asyncio.gather(*(batcher.embed_query(str(n)) for n in range(5)))
    assert [len(c) for c in embedder.calls] == [2, 2, 1]
    await batcher.close()


async def test_an_encoder_error_reaches_every_waiter_and_the_batcher_survives() -> None:
    embedder = FakeEmbedder(fail=True)
    batcher = QueryBatcher(embedder, max_wait_ms=10)  # type: ignore[arg-type]
    results = await asyncio.gather(
        batcher.embed_query("a"), batcher.embed_query("b"), return_exceptions=True
    )
    assert all(isinstance(r, RuntimeError) for r in results)
    embedder.fail = False
    assert float((await batcher.embed_query("c"))[0]) == 4.0
    await batcher.close()


@pytest.mark.parametrize("wait", [0])
async def test_zero_wait_still_answers(wait: float) -> None:
    batcher = QueryBatcher(FakeEmbedder(), max_wait_ms=wait)  # type: ignore[arg-type]
    assert float((await batcher.embed_query("ab"))[0]) == 5.0
    await batcher.close()
