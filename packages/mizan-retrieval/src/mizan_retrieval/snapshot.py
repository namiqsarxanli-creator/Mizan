"""Frozen corpus snapshots for reproducible evaluation (ADR-0005).

A snapshot is the exact list of chunk records an index was built from, as deterministic
gzipped JSON lines. It lets CI rebuild the search index without Postgres and without
downloading anything from e-qanun.az, and its content hash keys the embedding cache, so CI
re-embeds only when the corpus actually changed.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from mizan_ingestion.chunking import Strategy
from mizan_retrieval.corpus import ChunkRecord


def _row(record: ChunkRecord) -> dict[str, object]:
    return {
        "source_id": record.source_id,
        "strategy": record.strategy.value,
        "key": record.key,
        "parent_key": record.parent_key,
        "cites": list(record.cites),
        "text": record.text,
        "valid_from": record.valid_from.isoformat(),
        "valid_to": record.valid_to.isoformat(),
    }


def _sorted(records: Sequence[ChunkRecord]) -> list[ChunkRecord]:
    return sorted(records, key=lambda r: (r.source_id, r.strategy.value, r.key, r.valid_from))


def dumps(records: Sequence[ChunkRecord]) -> bytes:
    lines = (json.dumps(_row(r), ensure_ascii=False, sort_keys=True) for r in _sorted(records))
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    # mtime=0: identical records give byte-identical files (stable diffs and hashes).
    return gzip.compress(payload, mtime=0)


def write(path: Path, records: Sequence[ChunkRecord]) -> str:
    data = dumps(records)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return content_hash(records)


def read(path: Path) -> list[ChunkRecord]:
    records = []
    for line in gzip.decompress(path.read_bytes()).decode("utf-8").splitlines():
        row = json.loads(line)
        records.append(
            ChunkRecord(
                source_id=int(row["source_id"]),
                strategy=Strategy(row["strategy"]),
                key=row["key"],
                parent_key=row["parent_key"],
                cites=tuple(row["cites"]),
                text=row["text"],
                valid_from=date.fromisoformat(row["valid_from"]),
                valid_to=date.fromisoformat(row["valid_to"]),
            )
        )
    return records


def content_hash(records: Sequence[ChunkRecord]) -> str:
    digest = hashlib.sha256()
    for r in _sorted(records):
        digest.update(json.dumps(_row(r), ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return digest.hexdigest()[:16]
