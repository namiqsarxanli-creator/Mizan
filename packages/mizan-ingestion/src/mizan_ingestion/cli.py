"""Command line entry point.

mizan-ingest sync [ID ...] [--force-reparse]   sync documents (default: tracked ones)
mizan-ingest as-of ID DATE [--key art:69]       print a provision as it stood on DATE
mizan-ingest linked [ID ...]                     register linked acts, store court decisions
mizan-ingest diff ID KEY FROM TO                 how a provision changed between two dates
mizan-ingest inspect FILE.html                  parse a local file and print statistics
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from mizan_core.config import get_settings
from mizan_core.logging import configure_logging
from mizan_ingestion.db import repository as repo
from mizan_ingestion.db.session import create_engine, session_factory
from mizan_ingestion.decisions import sync_linked_acts
from mizan_ingestion.diff import PostgresDiff
from mizan_ingestion.parsing import parse_document
from mizan_ingestion.parsing.structure import flatten
from mizan_ingestion.pipeline import sync_document
from mizan_ingestion.sources.eqanun import SOURCE, EQanunClient


async def _sync(ids: list[int], force_reparse: bool) -> int:
    settings = get_settings()
    engine = create_engine(settings.postgres)
    try:
        async with EQanunClient(settings.ingestion) as client:
            for source_id in ids or settings.ingestion.tracked_documents:
                report = await sync_document(
                    client, session_factory(engine), source_id, force_reparse=force_reparse
                )
                print(
                    json.dumps(
                        {
                            "source_id": report.source_id,
                            "title": report.title,
                            "outcomes": {k.value: v for k, v in report.outcomes.items()},
                            "downloaded": report.downloaded,
                            "warnings": report.warnings,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
    finally:
        await engine.dispose()
    return 0


async def _as_of(source_id: int, on: date, key: str | None) -> int:
    engine = create_engine(get_settings().postgres)
    try:
        async with session_factory(engine)() as session:
            version, provisions = await repo.provisions_as_of(
                session, source=SOURCE, source_id=source_id, on=on, key_prefix=key
            )
    finally:
        await engine.dispose()
    if version is None:
        print(f"no version of {source_id} in force on {on}", file=sys.stderr)
        return 1
    estimated = " (estimated)" if version.valid_from_estimated else ""
    print(f"# version valid {version.valid_from}{estimated} .. {version.valid_to or 'now'}")
    for p in provisions:
        indent = "  " * max(p.depth - 2, 0)
        label = " ".join(x for x in (p.number and f"[{p.number}]", p.content.heading) if x)
        print(f"{indent}{label}".rstrip())
        if p.content.text:
            print(f"{indent}  {p.content.text}")
    return 0


async def _linked(ids: list[int]) -> int:
    settings = get_settings()
    engine = create_engine(settings.postgres)
    try:
        async with EQanunClient(settings.ingestion) as client:
            for source_id in ids or settings.ingestion.tracked_documents:
                report = await sync_linked_acts(client, session_factory(engine), source_id)
                summary = {
                    "source_id": source_id,
                    "acts": report.acts,
                    "by_type": dict(report.by_type.most_common()),
                    "decisions_stored": report.decisions_stored,
                    "failed": report.failed,
                }
                print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        await engine.dispose()
    return 0


async def _diff(source_id: int, key: str, start: date, end: date) -> int:
    engine = create_engine(get_settings().postgres)
    try:
        result = await PostgresDiff(session_factory(engine)).diff(source_id, key, start, end)
    finally:
        await engine.dispose()
    if result is None:
        print(f"no version of {source_id} in force on {start} or {end}", file=sys.stderr)
        return 1
    print(f"# {key}: text of {result.start_version_from} vs text of {result.end_version_from}")
    for a in result.amendments:
        print(f"- {a.adopted_on} {a.number or '(no number)'}: {a.note}")
    if result.unchanged:
        print("no change")
    for c in result.changes:
        print()
        print(f"[{c.kind.value}] {c.key}")
        print(c.marked)
    return 0


def _inspect(path: Path) -> int:
    parsed = parse_document(path.read_bytes())
    provisions, warnings = flatten(parsed.root)
    print(f"title:     {parsed.title}")
    print(f"encoding:  {parsed.encoding}")
    print(f"endnotes:  {len(parsed.endnotes)}")
    for kind, count in Counter(p.kind.value for p in provisions).most_common():
        print(f"{kind:>10}: {count}")
    for warning in [*parsed.warnings, *warnings]:
        print(f"WARNING: {warning}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mizan-ingest")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="sync documents from e-qanun.az")
    p_sync.add_argument("ids", nargs="*", type=int, help="e-qanun framework ids")
    p_sync.add_argument("--force-reparse", action="store_true")

    p_asof = sub.add_parser("as-of", help="show a document as it stood on a date")
    p_asof.add_argument("id", type=int)
    p_asof.add_argument("date", type=date.fromisoformat)
    p_asof.add_argument("--key", help="provision key prefix, e.g. art:69")

    p_linked = sub.add_parser("linked", help="register linked acts, store court decisions")
    p_linked.add_argument("ids", nargs="*", type=int, help="e-qanun ids of tracked documents")

    p_diff = sub.add_parser("diff", help="how a provision changed between two dates")
    p_diff.add_argument("id", type=int)
    p_diff.add_argument("key", help="provision key, e.g. art:69")
    p_diff.add_argument("start", type=date.fromisoformat)
    p_diff.add_argument("end", type=date.fromisoformat)

    p_inspect = sub.add_parser("inspect", help="parse a local HTML file")
    p_inspect.add_argument("path", type=Path)

    args = parser.parse_args(argv)
    # Legal text is Azerbaijani; never let a legacy console code page crash the output.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = get_settings()
    configure_logging(settings.log.level, json_format=settings.log.json_format, stream=sys.stderr)

    if args.command == "sync":
        return asyncio.run(_sync(args.ids, args.force_reparse))
    if args.command == "as-of":
        return asyncio.run(_as_of(args.id, args.date, args.key))
    if args.command == "linked":
        return asyncio.run(_linked(args.ids))
    if args.command == "diff":
        return asyncio.run(_diff(args.id, args.key, args.start, args.end))
    return _inspect(args.path)


if __name__ == "__main__":
    raise SystemExit(main())
