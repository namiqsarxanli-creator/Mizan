"""Turn a flat paragraph stream into the legal hierarchy.

Numbering conventions in Azerbaijani codes::

    I bölmə / Birinci fəsil                  section / chapter  (centred lines)
    Maddə 69. Heading                        article            ("Maddə 7-1." for inserted ones)
    3.  / 3-1.                               part (hissə)
    3.1. / 69.3.                             point (bənd)
    3.1.2.                                   subpoint
    a) / ç)                                  item
    ƏLAVƏLƏR / 1-ci əlavə / §1. Heading      annexes, annex, annex part

Unnumbered paragraphs continue the most recent node.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from mizan_ingestion.models import KEY_PREFIX, Node, NodeKind, Provision
from mizan_ingestion.parsing.word_html import Paragraph
from mizan_ingestion.text import az_lower, parse_ordinal, parse_roman

_NUM = r"\d+(?:-\d+)?"
SECTION_RE = re.compile(r"^(?P<num>[IVXLC]+)\s+bölmə(?:[.:]\s*(?P<title>.+))?$", re.IGNORECASE)
CHAPTER_RE = re.compile(r"^(?P<ord>[^\d.:]+?)\s+fəsil(?:[.:]\s*(?P<title>.+))?$")
CHAPTER_NUM_RE = re.compile(r"^fəsil\s+(?P<num>\d+|[ivxlc]+)(?:[.:]\s*(?P<title>.+))?$")
ARTICLE_RE = re.compile(rf"^Maddə\s+(?P<num>{_NUM})\.?\s*(?P<title>.*)$")
SUBPOINT_RE = re.compile(rf"^(?P<num>{_NUM}(?:\.{_NUM}){{2,}})\.\s*(?P<rest>.*)$")
POINT_RE = re.compile(rf"^(?P<num>{_NUM}\.{_NUM})\.\s*(?P<rest>.*)$")
PART_RE = re.compile(rf"^(?P<num>{_NUM})\.\s+(?P<rest>.*)$")
ITEM_RE = re.compile(r"^(?P<num>[a-zçəğıöşü])\)\s*(?P<rest>.*)$")
ANNEXES_RE = re.compile(r"^əlavələr$")
# "1-ci əlavə", or with its dedication on the same line: "... Əmək Məcəlləsinə 1-ci əlavə".
ANNEX_RE = re.compile(
    r"^(?:.*(?:məcəlləsinə|qanununa)\s+)?(?P<num>\d+)\s*-\s*c[iıuü]\s+əlavə"
    r"(?:[.:]\s*(?P<title>.+))?$"
)
# Dedication line printed above annexes: "Azərbaycan Respublikasının Əmək Məcəlləsinə".
ANNEX_DEDICATION_RE = re.compile(r"(?:məcəlləsinə|qanununa)$")
ANNEX_PART_RE = re.compile(r"^§\s*(?P<num>\d+(?:-\d+)?)\.?\s*(?P<title>.*)$")

_MAX_MARKER_LEN = 200

# Depth of each numbered level inside an article.
_LEVEL = {NodeKind.PART: 1, NodeKind.POINT: 2, NodeKind.SUBPOINT: 3, NodeKind.ITEM: 4}


def _match_heading(p: Paragraph) -> tuple[NodeKind, str, str | None] | None:
    """Section/chapter marker -> (kind, number, inline title or None).

    The patterns are full-line and specific ("Birinci fəsil"), so layout is not required:
    older exports do not centre these lines.
    """
    if len(p.text) > _MAX_MARKER_LEN:
        return None
    if m := SECTION_RE.match(p.text):
        return NodeKind.SECTION, m.group("num").upper(), m.group("title")
    lowered = az_lower(p.text)
    if m := CHAPTER_NUM_RE.match(lowered):
        raw = m.group("num")
        number = raw if raw.isdigit() else str(parse_roman(raw.upper()) or raw)
        return NodeKind.CHAPTER, number, _original_title(p.text, m.group("title"))
    if (m := CHAPTER_RE.match(lowered)) and (n := parse_ordinal(m.group("ord"))) is not None:
        return NodeKind.CHAPTER, str(n), _original_title(p.text, m.group("title"))
    return None


def _original_title(original: str, lowered_title: str | None) -> str | None:
    # Matching ran on lowercased text; return the title with its original casing.
    return original[len(original) - len(lowered_title) :] if lowered_title else None


def _match_numbered(text: str) -> tuple[NodeKind, str, str] | None:
    for kind, pattern in (
        (NodeKind.SUBPOINT, SUBPOINT_RE),
        (NodeKind.POINT, POINT_RE),
        (NodeKind.PART, PART_RE),
        (NodeKind.ITEM, ITEM_RE),
    ):
        if m := pattern.match(text):
            return kind, m.group("num"), m.group("rest")
    return None


@dataclass
class _Builder:
    root: Node
    warnings: list[str] = field(default_factory=list)
    section: Node | None = None
    chapter: Node | None = None
    article: Node | None = None
    # Open numbered nodes inside the current article, outermost first.
    stack: list[Node] = field(default_factory=list)
    pending_title: Node | None = None
    # Centred lines seen inside an article; they are either part of it or the header of the
    # annex that follows, which we only know once the next paragraph arrives.
    held: list[Paragraph] = field(default_factory=list)

    @property
    def container(self) -> Node:
        return self.chapter or self.section or self.root

    @property
    def deepest(self) -> Node:
        if self.stack:
            return self.stack[-1]
        return self.article or self.container

    def add_heading(self, kind: NodeKind, number: str, title: str | None, p: Paragraph) -> None:
        node = Node(kind, number=number, heading=title, endnote_refs=list(p.endnote_refs))
        if kind is NodeKind.SECTION:
            self.root.children.append(node)
            self.section, self.chapter = node, None
        else:
            (self.section or self.root).children.append(node)
            self.chapter = node
        self.article, self.stack = None, []
        self.pending_title = None if title else node

    def add_article(self, number: str, title: str, p: Paragraph) -> None:
        node = Node(NodeKind.ARTICLE, number, title or None, endnote_refs=list(p.endnote_refs))
        self.container.children.append(node)
        self.article, self.stack, self.pending_title = node, [], None

    def start_annexes(self) -> None:
        self.section = self.chapter = self.article = None
        self.stack, self.pending_title = [], None

    def add_annex(
        self, number: str, title: str | None, header: list[Paragraph], p: Paragraph
    ) -> None:
        node = Node(
            NodeKind.ANNEX,
            number,
            title,
            lines=[h.text for h in header],
            endnote_refs=[r for h in (*header, p) for r in h.endnote_refs],
        )
        self.root.children.append(node)
        self.section = self.chapter = None
        self.article, self.stack = node, []
        self.pending_title = None if title else node

    def add_numbered(
        self, kind: NodeKind, number: str, rest: str, p: Paragraph, heading: str | None = None
    ) -> None:
        if self.article is None:
            raise RuntimeError("numbered provision outside an article or annex")
        node = Node(
            kind,
            number,
            heading,
            lines=[rest] if rest else [],
            endnote_refs=list(p.endnote_refs),
        )
        level = _LEVEL[kind]
        while self.stack and _LEVEL[self.stack[-1].kind] >= level:
            self.stack.pop()
        if kind is NodeKind.POINT and self.stack:
            # "3.1." belongs to part "3."; "69.1." (article-prefixed numbering) to the article.
            head = number.split(".")[0]
            if self.stack[-1].kind is NodeKind.PART and self.stack[-1].number != head:
                self.stack.pop()
        parent = self.stack[-1] if self.stack else self.article
        parent.children.append(node)
        self.stack.append(node)

    def add_text(self, p: Paragraph) -> None:
        if self.pending_title is not None and (p.centered or p.emphasised or p.heading_tag):
            self.pending_title.heading = p.text
            self.pending_title.endnote_refs.extend(p.endnote_refs)
            self.pending_title = None
            return
        self.pending_title = None
        target = self.deepest
        target.lines.append(p.text)
        target.endnote_refs.extend(p.endnote_refs)

    def flush_held(self) -> None:
        held, self.held = self.held, []
        for h in held:
            self.add_text(h)

    def feed(self, p: Paragraph) -> None:
        lowered = az_lower(p.text)
        short = len(p.text) <= _MAX_MARKER_LEN
        if short and ANNEXES_RE.match(lowered):
            self.held = []  # e.g. "Azərbaycan Respublikasının Əmək Məcəlləsinə" above "ƏLAVƏLƏR"
            self.start_annexes()
            return
        if short and (m := ANNEX_RE.match(lowered)):
            header, self.held = self.held, []
            self.add_annex(m.group("num"), _original_title(p.text, m.group("title")), header, p)
            return
        in_article = self.article is not None and self.article.kind is NodeKind.ARTICLE
        possibly_annex_header = p.centered or (short and ANNEX_DEDICATION_RE.search(lowered))
        if possibly_annex_header and in_article and not _match_heading(p):
            self.held.append(p)
            return
        self.flush_held()

        in_annex = self.article is not None and self.article.kind is NodeKind.ANNEX
        if in_annex and (m := ANNEX_PART_RE.match(p.text)):
            self.add_numbered(NodeKind.PART, m.group("num"), "", p, heading=m.group("title"))
        elif heading := _match_heading(p):
            self.add_heading(*heading, p)
        elif (m := ARTICLE_RE.match(p.text)) and p.emphasised:
            self.add_article(m.group("num"), m.group("title"), p)
        elif in_article and (numbered := _match_numbered(p.text)):
            # Inside annexes (forms, tables) only "§" headings are structure; numbered lines
            # there are form fields or table rows.
            self.add_numbered(*numbered, p)
        else:
            self.add_text(p)


def build_tree(paragraphs: list[Paragraph], *, title: str) -> tuple[Node, list[str]]:
    builder = _Builder(root=Node(NodeKind.DOCUMENT, heading=title))
    for p in paragraphs:
        builder.feed(p)
    builder.flush_held()
    return builder.root, builder.warnings


def flatten(root: Node) -> tuple[list[Provision], list[str]]:
    """Depth-first flattening with stable, version-independent keys."""
    provisions: list[Provision] = []
    seen: dict[str, int] = {}
    warnings: list[str] = []

    def key_for(node: Node, parent_key: str | None) -> str:
        own = f"{KEY_PREFIX[node.kind]}:{node.number}"
        # Articles are unique within a code, so their key does not depend on the chapter:
        # renumbering chapters must not change article keys.
        nested = node.kind not in {
            NodeKind.SECTION,
            NodeKind.CHAPTER,
            NodeKind.ARTICLE,
            NodeKind.ANNEX,
        }
        key = f"{parent_key}/{own}" if nested and parent_key else own
        if key in seen:
            seen[key] += 1
            warnings.append(f"duplicate provision key {key!r}; stored as {key}#{seen[key]}")
            key = f"{key}#{seen[key]}"
        else:
            seen[key] = 1
        return key

    def visit(node: Node, parent_key: str | None, depth: int) -> None:
        key = key_for(node, parent_key)
        provisions.append(
            Provision(
                key=key,
                parent_key=parent_key,
                kind=node.kind,
                number=node.number,
                heading=node.heading,
                text=node.text,
                ordinal=len(provisions),
                depth=depth,
                endnote_refs=tuple(dict.fromkeys(node.endnote_refs)),
            )
        )
        for child in node.children:
            visit(child, key, depth + 1)

    for child in root.children:
        visit(child, None, 0)
    return provisions, warnings
