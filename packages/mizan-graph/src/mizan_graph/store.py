"""Graph storage: Neo4j in production, an in-memory twin for tests and the CI eval gate.

Schema (ADR-0006)::

    (:Act {uid, source_id?, title?})
    (:Article {uid, act, key})-[:PART_OF]->(:Act)
    (:Article)-[:REFERENCES {valid_from, valid_to, numbers}]->(:Article)
    (:AmendingAct {uid, number, adopted_on})-[:AMENDS {valid_from}]->(:Article)
    (:CourtDecision {uid, eqanun_id})-[:INTERPRETS]->(:Article)

``valid_from``/``valid_to`` are day ordinals, as in the Qdrant payload, so "edges in force on a
date" is a plain integer comparison. Article uids are ``"<act uid>/<key>"``; the act uid is
``eqanun:<id>`` for ingested acts and ``name:<normalised name>`` for acts known only from
citations (the Constitution, the Tax Code...), which become nodes as soon as we ingest them.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, LiteralString, Protocol

from neo4j import AsyncDriver

from mizan_graph.build import Reference
from mizan_graph.citations import SELF


class Direction(StrEnum):
    OUT = "out"  # articles the seed cites
    IN = "in"  # articles citing the seed
    BOTH = "both"


@dataclass(frozen=True, slots=True)
class Neighbour:
    seed: str  # article key the edge starts from (in the query's terms)
    key: str  # neighbouring article key
    direction: Direction


class GraphStore(Protocol):
    async def neighbours(
        self, source_id: int, keys: Collection[str], *, as_of: date, direction: Direction
    ) -> list[Neighbour]:
        """Articles of the same act linked to ``keys`` by citations in force on ``as_of``."""
        ...


def act_uid(source_id: int) -> str:
    return f"eqanun:{source_id}"


def _target_act(source_id: int, act: str) -> str:
    return act_uid(source_id) if act == SELF else f"name:{act}"


class InMemoryGraph:
    """The same queries over a list of references; built from a corpus snapshot."""

    def __init__(self, source_id: int, references: Sequence[Reference]) -> None:
        self._source_id = source_id
        self._out: dict[str, list[Reference]] = defaultdict(list)
        self._in: dict[str, list[Reference]] = defaultdict(list)
        for ref in references:
            if ref.internal:
                self._out[ref.source].append(ref)
                self._in[ref.target].append(ref)

    async def neighbours(
        self, source_id: int, keys: Collection[str], *, as_of: date, direction: Direction
    ) -> list[Neighbour]:
        if source_id != self._source_id:
            return []
        found: list[Neighbour] = []
        for key in keys:
            if direction in (Direction.OUT, Direction.BOTH):
                found += [
                    Neighbour(key, r.target, Direction.OUT)
                    for r in self._out.get(key, ())
                    if r.valid_from <= as_of < r.valid_to
                ]
            if direction in (Direction.IN, Direction.BOTH):
                found += [
                    Neighbour(key, r.source, Direction.IN)
                    for r in self._in.get(key, ())
                    if r.valid_from <= as_of < r.valid_to
                ]
        return found


_SCHEMA = [
    "CREATE CONSTRAINT act_uid IF NOT EXISTS FOR (n:Act) REQUIRE n.uid IS UNIQUE",
    "CREATE CONSTRAINT article_uid IF NOT EXISTS FOR (n:Article) REQUIRE n.uid IS UNIQUE",
    "CREATE CONSTRAINT amending_act_uid IF NOT EXISTS FOR (n:AmendingAct) REQUIRE n.uid IS UNIQUE",
    "CREATE CONSTRAINT decision_uid IF NOT EXISTS FOR (n:CourtDecision) REQUIRE n.uid IS UNIQUE",
    "CREATE INDEX article_act IF NOT EXISTS FOR (n:Article) ON (n.act)",
]

_WRITE_REFERENCES = """
UNWIND $rows AS row
MERGE (sa:Act {uid: row.source_act})
MERGE (ta:Act {uid: row.target_act})
MERGE (s:Article {uid: row.source_act + '/' + row.source})
  ON CREATE SET s.act = row.source_act, s.key = row.source
MERGE (t:Article {uid: row.target_act + '/' + row.target})
  ON CREATE SET t.act = row.target_act, t.key = row.target
MERGE (s)-[:PART_OF]->(sa)
MERGE (t)-[:PART_OF]->(ta)
CREATE (s)-[:REFERENCES {valid_from: row.valid_from, valid_to: row.valid_to,
                         numbers: row.numbers}]->(t)
"""

_NEIGHBOURS = """
UNWIND $uids AS uid
MATCH (s:Article {uid: uid})
CALL (s) {
  MATCH (s)-[r:REFERENCES]->(t:Article {act: $act})
  WHERE $outgoing AND r.valid_from <= $day < r.valid_to
  RETURN t.key AS key, 'out' AS direction
  UNION
  MATCH (s)<-[r:REFERENCES]-(t:Article {act: $act})
  WHERE $incoming AND r.valid_from <= $day < r.valid_to
  RETURN t.key AS key, 'in' AS direction
}
RETURN s.key AS seed, key, direction
"""


class Neo4jGraph:
    def __init__(self, driver: AsyncDriver, *, database: str | None = None) -> None:
        self._driver = driver
        self._database = database

    async def _run(self, query: LiteralString, **params: Any) -> list[dict[str, Any]]:
        result = await self._driver.execute_query(
            query,
            params,
            database_=self._database,
        )
        return [record.data() for record in result.records]

    async def ensure_schema(self) -> None:
        for statement in _SCHEMA:
            await self._run(statement)

    async def replace_references(
        self, source_id: int, title: str, references: Sequence[Reference], *, batch: int = 500
    ) -> None:
        """Replace every citation edge out of ``source_id``'s articles (an idempotent rebuild)."""
        act = act_uid(source_id)
        await self._run(
            "MERGE (a:Act {uid: $act}) SET a.source_id = $source_id, a.title = $title",
            act=act,
            source_id=source_id,
            title=title,
        )
        await self._run("MATCH (:Article {act: $act})-[r:REFERENCES]->() DELETE r", act=act)
        rows = [
            {
                "source_act": act,
                "source": r.source,
                "target_act": _target_act(source_id, r.act),
                "target": r.target,
                "valid_from": r.valid_from.toordinal(),
                "valid_to": r.valid_to.toordinal(),
                "numbers": list(r.numbers),
            }
            for r in references
        ]
        for start in range(0, len(rows), batch):
            await self._run(_WRITE_REFERENCES, rows=rows[start : start + batch])

    async def neighbours(
        self, source_id: int, keys: Collection[str], *, as_of: date, direction: Direction
    ) -> list[Neighbour]:
        act = act_uid(source_id)
        records = await self._run(
            _NEIGHBOURS,
            uids=[f"{act}/{k}" for k in keys],
            act=act,
            day=as_of.toordinal(),
            outgoing=direction in (Direction.OUT, Direction.BOTH),
            incoming=direction in (Direction.IN, Direction.BOTH),
        )
        return [Neighbour(r["seed"], r["key"], Direction(r["direction"])) for r in records]

    async def counts(self) -> dict[str, int]:
        rows = await self._run(
            "MATCH (n) WITH labels(n)[0] AS label, count(*) AS n RETURN label, n "
            "UNION ALL MATCH ()-[r]->() WITH type(r) AS label, count(*) AS n RETURN label, n"
        )
        return {str(r["label"]): int(r["n"]) for r in rows}
