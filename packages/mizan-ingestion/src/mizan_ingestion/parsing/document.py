"""Raw bytes -> ParsedDocument."""

from __future__ import annotations

from mizan_ingestion.models import NodeKind, ParsedDocument
from mizan_ingestion.parsing.encoding import decode_html
from mizan_ingestion.parsing.endnotes import parse_endnotes
from mizan_ingestion.parsing.structure import build_tree
from mizan_ingestion.parsing.word_html import parse_word_html

# Bump whenever parsing output can change; stored versions parsed by an older parser are
# re-parsed on the next sync.
PARSER_VERSION = 4

# Some genuine texts quote Russian names; a handful of Cyrillic letters is not mis-decoding.
_MAX_CYRILLIC = 20


def parse_document(raw: bytes) -> ParsedDocument:
    decoded = decode_html(raw)
    word = parse_word_html(decoded.text)

    # Word's <title> is template noise; the document opens with its real title.
    title = word.paragraphs[0].text if word.paragraphs else word.title
    root, warnings = build_tree(word.paragraphs, title=title)
    endnotes = parse_endnotes(word.endnotes)

    if decoded.cyrillic_chars > _MAX_CYRILLIC:
        warnings.append(
            f"{decoded.cyrillic_chars} Cyrillic characters after decoding as {decoded.encoding}; "
            "text may be mis-decoded"
        )
    articles = [n for n in root.walk() if n.kind is NodeKind.ARTICLE]
    if not articles:
        warnings.append("no articles found")
    dangling = sorted({r for n in root.walk() for r in n.endnote_refs} - endnotes.keys())
    if dangling:
        warnings.append(f"endnote references without a body: {dangling[:10]}")
    unparsed = [n for n, e in endnotes.items() if e.act is None]
    if unparsed:
        warnings.append(f"{len(unparsed)} endnotes without a recognisable amending act")

    return ParsedDocument(
        title=title, root=root, endnotes=endnotes, encoding=decoded.encoding, warnings=warnings
    )
