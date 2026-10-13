"""Domain model of a parsed legal document.

A document is a tree:  DOCUMENT > SECTION (bölmə) > CHAPTER (fəsil) > ARTICLE (maddə)
> PART (hissə, "3-1.") > POINT (bənd, "3.1.") > SUBPOINT ("3.1.2.") / ITEM ("a)").
Annexes (əlavələr: model forms, lists) follow the articles and are structured like articles.

Every node below the chapter level gets a stable ``key`` (``art:69/part:3-1``) that stays the
same across versions of the document. Keys are what lets us track a provision through time,
diff versions and cite it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum


class NodeKind(StrEnum):
    DOCUMENT = "document"
    SECTION = "section"
    CHAPTER = "chapter"
    ARTICLE = "article"
    PART = "part"
    POINT = "point"
    SUBPOINT = "subpoint"
    ITEM = "item"
    ANNEX = "annex"


# Prefixes used in provision keys.
KEY_PREFIX: dict[NodeKind, str] = {
    NodeKind.SECTION: "sec",
    NodeKind.CHAPTER: "ch",
    NodeKind.ARTICLE: "art",
    NodeKind.PART: "part",
    NodeKind.POINT: "pt",
    NodeKind.SUBPOINT: "spt",
    NodeKind.ITEM: "item",
    NodeKind.ANNEX: "annex",
}


@dataclass(slots=True)
class Node:
    kind: NodeKind
    number: str | None = None
    heading: str | None = None
    lines: list[str] = field(default_factory=list)
    endnote_refs: list[int] = field(default_factory=list)
    children: list[Node] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def walk(self) -> list[Node]:
        out: list[Node] = [self]
        for child in self.children:
            out.extend(child.walk())
        return out


@dataclass(frozen=True, slots=True)
class AmendingAct:
    """The act that introduced a change, parsed from an endnote."""

    number: str | None  # e.g. "332-VIIQD"; a few source endnotes omit it
    adopted_on: date


@dataclass(frozen=True, slots=True)
class Endnote:
    number: int
    text: str
    act: AmendingAct | None


@dataclass(frozen=True, slots=True)
class Provision:
    """A flattened node, ready to be stored."""

    key: str
    parent_key: str | None
    kind: NodeKind
    number: str | None
    heading: str | None
    text: str
    ordinal: int
    depth: int
    endnote_refs: tuple[int, ...]

    @property
    def content_hash(self) -> str:
        payload = f"{self.kind}\x1f{self.number or ''}\x1f{self.heading or ''}\x1f{self.text}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class ParsedDocument:
    title: str
    root: Node
    endnotes: dict[int, Endnote]
    encoding: str
    warnings: list[str] = field(default_factory=list)

    @property
    def articles(self) -> list[Node]:
        return [n for n in self.root.walk() if n.kind is NodeKind.ARTICLE]
