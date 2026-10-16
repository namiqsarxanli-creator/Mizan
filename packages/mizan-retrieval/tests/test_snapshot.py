from dataclasses import replace
from datetime import date
from pathlib import Path

from mizan_ingestion.chunking import Strategy
from mizan_retrieval import snapshot
from mizan_retrieval.corpus import OPEN_END, ChunkRecord


def rec(key: str, text: str, start: date, end: date = OPEN_END) -> ChunkRecord:
    return ChunkRecord(46943, Strategy.ARTICLE, key, key, (key,), text, start, end)


RECORDS = [
    rec("art:69", "Maddə 69. Xitam", date(2025, 6, 13)),
    rec("art:69", "Köhnə mətn", date(1999, 2, 1), date(2025, 6, 13)),
    rec("art:1", "Maddə 1.", date(1999, 2, 1)),
]


def test_round_trip_preserves_every_field(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl.gz"
    snapshot.write(path, RECORDS)
    assert sorted(snapshot.read(path), key=lambda r: (r.key, r.valid_from)) == sorted(
        RECORDS, key=lambda r: (r.key, r.valid_from)
    )


def test_output_is_byte_identical_regardless_of_input_order() -> None:
    assert snapshot.dumps(RECORDS) == snapshot.dumps(list(reversed(RECORDS)))
    assert snapshot.content_hash(RECORDS) == snapshot.content_hash(list(reversed(RECORDS)))


def test_hash_changes_with_the_text() -> None:
    changed = [*RECORDS[:2], replace(RECORDS[2], text="Dəyişib")]
    assert snapshot.content_hash(changed) != snapshot.content_hash(RECORDS)
