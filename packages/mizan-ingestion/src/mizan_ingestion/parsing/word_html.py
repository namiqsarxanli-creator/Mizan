"""Extract a flat paragraph stream from Microsoft Word "Save as HTML" output.

Word HTML is layout, not structure: there are no heading tags, only ``<p class=MsoNormal>``
with inline styles. What survives and what we rely on:

* paragraph order and text,
* centring (``align=center`` / ``text-align:center``) for section and chapter titles,
* bold runs or Word heading styles (``<h3>``, used by older exports) for article headings,
* endnote anchors (``<a href="#_edn204">[204]</a>``) and the endnote bodies
  (``<div id=edn204>``), which carry the amendment history,
* links to other acts on e-qanun (``<a href=".../framework/48189">KMQ6</a>``): the "KMQ"
  markers after article headings point to Constitutional Court decisions interpreting them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import cast

from lxml import html as lxml_html
from lxml.html import HtmlElement

from mizan_ingestion.text import normalize

_EDN_HREF = re.compile(r"#_edn(\d+)$")
_EDN_ID = re.compile(r"^edn(\d+)$")
_ACT_HREF = re.compile(r"e-qanun\.az/framework/(\d+)")
# Reference markers that link to an act and carry no text of their own ("KMQ6", "KM4").
_MARKER_TEXT = re.compile(r"^\s*KMQ?\s*\d+\s*$")
_EDN_MARKER = re.compile(r"^\s*\[\d+\]\s*")


@dataclass(frozen=True, slots=True)
class Paragraph:
    text: str
    centered: bool
    bold: bool
    endnote_refs: tuple[int, ...]
    heading_tag: bool = False
    # e-qanun ids of acts linked from this paragraph (e.g. Constitutional Court decisions).
    linked_acts: tuple[int, ...] = ()

    @property
    def emphasised(self) -> bool:
        """Visually marked as a heading, whichever way the export expressed it."""
        return self.bold or self.heading_tag


@dataclass(frozen=True, slots=True)
class WordDocument:
    title: str
    paragraphs: list[Paragraph]
    endnotes: dict[int, str]


def _is_centered(el: HtmlElement) -> bool:
    style = (el.get("style") or "").replace(" ", "").lower()
    return (el.get("align") or "").lower() == "center" or "text-align:center" in style


def _paragraph(el: HtmlElement) -> Paragraph | None:
    refs: list[int] = []
    acts: list[int] = []
    for anchor in el.iterfind(".//a[@href]"):
        href = anchor.get("href", "")
        if match := _EDN_HREF.search(href):
            refs.append(int(match.group(1)))
            # Drop the "[204]" marker from the text but keep whatever follows the anchor.
            anchor.drop_tree()
        elif match := _ACT_HREF.search(href):
            acts.append(int(match.group(1)))
            if _MARKER_TEXT.match(anchor.text_content()):
                anchor.drop_tree()
    text = normalize(el.text_content())
    if not text:
        return None
    bold_text = normalize("".join(b.text_content() for b in el.iterfind(".//b")))
    # "Bold" means the paragraph *starts* bold: article headings, not a bold word mid-sentence.
    return Paragraph(
        text=text,
        centered=_is_centered(el),
        bold=bool(bold_text) and text.startswith(bold_text[:20]),
        endnote_refs=tuple(dict.fromkeys(refs)),
        heading_tag=el.tag in {"h1", "h2", "h3"},
        linked_acts=tuple(dict.fromkeys(acts)),
    )


def parse_word_html(html_text: str) -> WordDocument:
    # lxml's stubs type this as a bare _Element; with the HTML parser it is an HtmlElement.
    root = cast(HtmlElement, lxml_html.document_fromstring(html_text))
    body = root.body

    endnotes: dict[int, str] = {}
    for div in body.xpath(".//div[starts-with(@id, 'edn')]"):
        match = _EDN_ID.match(div.get("id", ""))
        if match:
            endnotes[int(match.group(1))] = _EDN_MARKER.sub("", normalize(div.text_content()))

    paragraphs: list[Paragraph] = []
    for el in body.xpath(
        ".//p[not(ancestor::div[starts-with(@id, 'edn')])]"
        "|.//h1[not(ancestor::div[starts-with(@id, 'edn')])]"
        "|.//h2[not(ancestor::div[starts-with(@id, 'edn')])]"
        "|.//h3[not(ancestor::div[starts-with(@id, 'edn')])]"
    ):
        paragraph = _paragraph(el)
        if paragraph:
            paragraphs.append(paragraph)

    title_el = root.find(".//title")
    title = normalize("".join(title_el.itertext())) if title_el is not None else ""
    return WordDocument(title=title, paragraphs=paragraphs, endnotes=endnotes)
