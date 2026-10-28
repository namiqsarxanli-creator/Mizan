"""Semantic answer cache: reuse an answer for a question that means the same (F9).

Two people rarely type the same question, so an exact-match cache would almost never hit; a
semantic cache matches on the question's embedding. The danger is the opposite mistake: two
questions about the same article that *differ* ("how long is the probation period?" vs "can
probation be extended?") embed close together, and returning one's answer for the other is a
wrong legal answer. Three guards:

1. **Similarity threshold**, calibrated on paraphrase pairs (should hit) against different
   questions about the same articles (must not hit). See ``mizan-eval cache``.
2. **Evidence fingerprint (temporal invalidation).** An entry stores the provisions its answer
   cites, each with the date its text took effect. It is served only if every one of them is
   among the current request's retrieved sources *in the same version*. When an amendment
   changes a cited text, or a question about another date retrieves another version, the entry
   no longer applies. No TTL guesswork about when the law changed.
3. **Prompt and model version.** An entry made by another prompt or model is not reused.

Retrieval still runs on every request (it is cheap); the cache saves the LLM call, which is
where the cost and the latency are.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from qdrant_client import AsyncQdrantClient, models

from mizan_core.logging import get_logger
from mizan_retrieval.answer import Answer, Citation
from mizan_retrieval.search import Hit

log = get_logger(__name__)

COLLECTION = "answer_cache"


@dataclass(frozen=True, slots=True)
class CacheHit:
    answer: Answer
    similarity: float
    question: str  # the cached question that matched


def fingerprint(hits: Sequence[Hit]) -> dict[str, str]:
    """Provision key -> the date its text took effect, for the sources of one request."""
    return {h.key: h.valid_from.isoformat() for h in hits}


def still_valid(cited: dict[str, str], current: dict[str, str]) -> bool:
    """Every cited provision is retrieved again, in the same version."""
    return bool(cited) and all(current.get(key) == since for key, since in cited.items())


class SemanticCache:
    def __init__(
        self,
        client: AsyncQdrantClient,
        *,
        dim: int,
        threshold: float,
        ttl_s: int,
        version: str,
        candidates: int = 3,
        now: Any = time.time,
    ) -> None:
        self._client = client
        self._dim = dim
        self.threshold = threshold
        self._ttl_s = ttl_s
        self._version = version  # prompt version + model
        self._candidates = candidates
        self._now = now
        self._ready = False

    async def _ensure(self) -> None:
        if self._ready:
            return
        if not await self._client.collection_exists(COLLECTION):
            await self._client.create_collection(
                COLLECTION,
                vectors_config=models.VectorParams(size=self._dim, distance=models.Distance.COSINE),
            )
            for field, schema in (
                ("version", models.PayloadSchemaType.KEYWORD),
                ("created", models.PayloadSchemaType.FLOAT),
            ):
                await self._client.create_payload_index(COLLECTION, field, field_schema=schema)
        self._ready = True

    async def lookup(self, vector: Sequence[float], hits: Sequence[Hit]) -> CacheHit | None:
        await self._ensure()
        found = await self._client.query_points(
            COLLECTION,
            query=list(vector),
            limit=self._candidates,
            score_threshold=self.threshold,
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="version", match=models.MatchValue(value=self._version)
                    ),
                    models.FieldCondition(
                        key="created", range=models.Range(gte=self._now() - self._ttl_s)
                    ),
                ]
            ),
            with_payload=True,
        )
        current = fingerprint(hits)
        for point in found.points:
            payload = point.payload or {}
            if still_valid(payload.get("cited", {}), current):
                return CacheHit(_answer(payload), point.score, payload.get("question", ""))
        return None

    async def store(
        self, vector: Sequence[float], question: str, answer: Answer, hits: Sequence[Hit]
    ) -> None:
        """Cache an answer that cites at least one retrieved source. Refusals are not cached:
        a refusal is cheap to recompute and may be a retrieval miss worth retrying."""
        current = fingerprint(hits)
        cited = {c.key: current[c.key] for c in answer.citations if c.key in current}
        if answer.refused or not cited:
            return
        await self._ensure()
        await self._client.upsert(
            COLLECTION,
            points=[
                models.PointStruct(
                    id=str(uuid.uuid4()),
                    vector=list(vector),
                    payload={
                        "version": self._version,
                        "created": self._now(),
                        "question": question,
                        "cited": cited,
                        "text": answer.text,
                        "citations": [[c.key, c.title, c.quote] for c in answer.citations],
                        "model": answer.model,
                    },
                )
            ],
        )


def _answer(payload: dict[str, Any]) -> Answer:
    return Answer(
        text=payload["text"],
        citations=tuple(Citation(k, t, q) for k, t, q in payload["citations"]),
        model=payload["model"],
        stop_reason="cache",
    )
