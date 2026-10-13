"""Chunking strategies over parsed provisions.

Three strategies, to be compared on the retrieval eval set in F2 (ADR-0003 explains why the
choice is measured, not assumed):

* ``article_chunks``  one chunk per article. If an article is too long, it is split into its
  parts, and each part keeps a pointer to the article (parent-child retrieval: search small,
  read the whole article).
* ``part_chunks``     one chunk per part (hissə), always with its article as parent.
* ``fixed_chunks``    structure-blind sliding window: the baseline every smarter strategy must
  beat.

Every structural chunk starts with a breadcrumb ("Əmək Məcəlləsi > III bölmə ... > Maddə 69")
so that the embedding sees where the text sits, and carries the provision key for citations.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from mizan_ingestion.models import NodeKind, Provision
from mizan_ingestion.text import ordinal_suffix


class Strategy(StrEnum):
    ARTICLE = "article"
    PART = "part"
    FIXED = "fixed"


@dataclass(frozen=True, slots=True)
class Chunk:
    strategy: Strategy
    key: str  # provision key, or "doc#<n>" for fixed chunks
    text: str  # what gets embedded: breadcrumb + body
    parent_key: str | None = None  # article to expand to for parent-child retrieval
    cites: tuple[str, ...] = field(default_factory=tuple)  # provision keys covered


def _ordinal(number: str) -> str:
    return ordinal_suffix(int(number)) if number.isdigit() else number


_LABEL = {
    NodeKind.SECTION: lambda n: f"{n} bölmə",
    NodeKind.CHAPTER: lambda n: f"{_ordinal(n)} fəsil",
    NodeKind.ARTICLE: lambda n: f"Maddə {n}.",
    NodeKind.ANNEX: lambda n: f"{_ordinal(n)} əlavə.",
}


def _title(p: Provision) -> str:
    label = _LABEL[p.kind](p.number) if p.kind in _LABEL and p.number else ""
    return " ".join(x for x in (label, p.heading) if x)


def _line(p: Provision) -> str:
    """Render a numbered provision the way it is printed: "3-1. text", "a) text"."""
    if p.kind is NodeKind.ITEM:
        prefix = f"{p.number})"
    elif p.kind in {NodeKind.PART, NodeKind.POINT, NodeKind.SUBPOINT}:
        prefix = f"{p.number}."
    else:
        prefix = ""
    body = " ".join(x for x in (p.heading, p.text) if x)
    return " ".join(x for x in (prefix, body) if x)


class _Index:
    def __init__(self, provisions: Sequence[Provision], document_title: str) -> None:
        self.by_key = {p.key: p for p in provisions}
        self.ordered = sorted(provisions, key=lambda p: p.ordinal)
        self.document_title = document_title

    def breadcrumb(self, p: Provision) -> str:
        trail: list[str] = []
        node: Provision | None = p
        while node is not None:
            if node.kind in _LABEL:
                trail.append(_title(node))
            node = self.by_key.get(node.parent_key) if node.parent_key else None
        return " > ".join([self.document_title, *reversed(trail)])

    def subtree(self, root: Provision) -> list[Provision]:
        prefix = f"{root.key}/"
        return [p for p in self.ordered if p.key.startswith(prefix)]

    def containers(self) -> list[Provision]:
        return [p for p in self.ordered if p.kind in {NodeKind.ARTICLE, NodeKind.ANNEX}]


def _article_body(article: Provision, descendants: list[Provision]) -> str:
    lines = [article.text] if article.text else []
    lines += [_line(d) for d in descendants]
    return "\n".join(lines)


def _part_chunks_of(index: _Index, article: Provision) -> list[Chunk]:
    chunks = []
    descendants = index.subtree(article)
    crumb = index.breadcrumb(article)
    if article.text:  # lead-in text before the first part
        chunks.append(
            Chunk(
                Strategy.PART, article.key, f"{crumb}\n{article.text}", article.key, (article.key,)
            )
        )
    for part in (d for d in descendants if d.parent_key == article.key):
        family = [part, *index.subtree(part)]
        body = "\n".join(_line(p) for p in family)
        chunks.append(
            Chunk(
                Strategy.PART,
                part.key,
                f"{crumb}\n{body}",
                parent_key=article.key,
                cites=tuple(p.key for p in family),
            )
        )
    return chunks


def article_chunks(
    provisions: Sequence[Provision], *, document_title: str, max_chars: int = 4000
) -> list[Chunk]:
    index = _Index(provisions, document_title)
    chunks: list[Chunk] = []
    for article in index.containers():
        descendants = index.subtree(article)
        text = f"{index.breadcrumb(article)}\n{_article_body(article, descendants)}"
        if len(text) <= max_chars or not descendants:
            cites = (article.key, *(d.key for d in descendants))
            chunks.append(Chunk(Strategy.ARTICLE, article.key, text, article.key, cites))
        else:
            chunks.extend(
                Chunk(Strategy.ARTICLE, c.key, c.text, c.parent_key, c.cites)
                for c in _part_chunks_of(index, article)
            )
    return chunks


def part_chunks(provisions: Sequence[Provision], *, document_title: str) -> list[Chunk]:
    index = _Index(provisions, document_title)
    chunks: list[Chunk] = []
    for article in index.containers():
        parts = _part_chunks_of(index, article)
        if parts:
            chunks.extend(parts)
        else:  # article without parts: the article itself is the smallest unit
            text = f"{index.breadcrumb(article)}\n{article.text}"
            chunks.append(Chunk(Strategy.PART, article.key, text, article.key, (article.key,)))
    return chunks


def fixed_chunks(
    provisions: Sequence[Provision], *, size: int = 1500, overlap: int = 200
) -> list[Chunk]:
    if not 0 <= overlap < size:
        raise ValueError("overlap must be in [0, size)")
    ordered = sorted(provisions, key=lambda p: p.ordinal)
    full = "\n".join(
        line for p in ordered if (line := " ".join(x for x in (_title(p), _line(p)) if x))
    )
    chunks: list[Chunk] = []
    start = 0
    while start < len(full):
        end = min(start + size, len(full))
        if end < len(full):
            # Prefer to cut at a line or word boundary in the last fifth of the window.
            cut = max(full.rfind("\n", start, end), full.rfind(" ", start, end))
            if cut > start + size * 4 // 5:
                end = cut
        chunks.append(Chunk(Strategy.FIXED, f"doc#{len(chunks)}", full[start:end].strip()))
        if end >= len(full):
            break
        start = end - overlap
    return chunks
