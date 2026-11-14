"""Text out of an uploaded contract: plain text, DOCX, PDF, or a photo or scan.

- **Text layer first.** A PDF page with a usable text layer is read with ``pypdf``: exact,
  free and fast.
- **OCR only where needed.** A page with (almost) no text is a scan. It is rendered to an image
  (``pypdfium2``) and handed to an ``Ocr`` backend, a vision model by default. Photos (PNG,
  JPEG) go to the same backend. Without a backend, scanned pages are reported, not guessed.

Every page records how it was read, so a report can say "pages 2-3 were OCR'd".
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePath
from typing import Literal, Protocol

from mizan_ingestion.text import normalize

Method = Literal["text", "docx", "pdf_text", "ocr", "unreadable"]

MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 40
# Fewer characters than this on a PDF page means a scan (or a blank page).
MIN_TEXT_CHARS = 40
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


class DocumentError(ValueError):
    """The upload cannot be read (unsupported type, too large, corrupt)."""


class Ocr(Protocol):
    async def read(self, image: bytes, media_type: str) -> str: ...


@dataclass(frozen=True, slots=True)
class Page:
    number: int  # 1-based
    text: str
    method: Method


@dataclass(frozen=True, slots=True)
class Document:
    filename: str
    pages: tuple[Page, ...]

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.pages if p.text)

    @property
    def ocr_pages(self) -> list[int]:
        return [p.number for p in self.pages if p.method == "ocr"]

    @property
    def unreadable_pages(self) -> list[int]:
        return [p.number for p in self.pages if p.method == "unreadable"]


def _clean(text: str) -> str:
    # normalize() folds whitespace to single spaces; keep line structure for clause splitting.
    lines = (normalize(line) for line in text.replace("\r", "\n").split("\n"))
    return "\n".join(line for line in lines if line)


def _docx(data: bytes) -> str:
    import docx  # local: a heavy import only DOCX uploads need

    try:
        doc = docx.Document(io.BytesIO(data))
    except Exception as exc:  # python-docx raises several unrelated types on bad input
        raise DocumentError("not a readable DOCX file") from exc
    lines = [p.text for p in doc.paragraphs]
    for table in doc.tables:  # contracts often put the parties' details in a table
        for row in table.rows:
            lines.append(" | ".join(cell.text.strip() for cell in row.cells))
    return "\n".join(lines)


def _render(data: bytes, index: int) -> bytes:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        bitmap = pdf[index].render(scale=2)  # ~144 dpi: enough for print, small enough to send
        out = io.BytesIO()
        bitmap.to_pil().convert("L").save(out, format="PNG", optimize=True)
        return out.getvalue()
    finally:
        pdf.close()


def _pdfium_text(data: bytes, count: int) -> list[str] | None:
    """Each page's text layer read by pdfium; None if pdfium cannot read the file."""
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError:
        return None
    try:
        if len(pdf) != count:
            return None
        # _clean splits on both \r and \n, so pdfium's \r\n line ends need no handling here.
        return [pdf[i].get_textpage().get_text_range() for i in range(count)]
    finally:
        pdf.close()


async def _pdf(data: bytes, ocr: Ocr | None) -> list[Page]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise DocumentError("encrypted PDFs are not supported")
        pages = list(reader.pages)
    except PdfReadError as exc:
        raise DocumentError("not a readable PDF file") from exc
    if len(pages) > MAX_PAGES:
        raise DocumentError(f"at most {MAX_PAGES} pages")
    layer = _pdfium_text(data, len(pages))
    out: list[Page] = []
    for i, page in enumerate(pages):
        # pdfium keeps words whole where pypdf splits them at letters a web font draws as
        # separate runs ("Az ə rbaycan", "qada ğ an"); pypdf stays the fallback.
        text = _clean(layer[i] if layer is not None else (page.extract_text() or ""))
        if len(text) >= MIN_TEXT_CHARS:
            out.append(Page(i + 1, text, "pdf_text"))
        elif ocr is not None:
            out.append(Page(i + 1, _clean(await ocr.read(_render(data, i), "image/png")), "ocr"))
        else:
            out.append(Page(i + 1, text, "unreadable"))
    return out


async def read_document(data: bytes, filename: str, *, ocr: Ocr | None = None) -> Document:
    if len(data) > MAX_BYTES:
        raise DocumentError(f"at most {MAX_BYTES // (1024 * 1024)} MB")
    suffix = PurePath(filename).suffix.lower()
    pages: Sequence[Page]
    if suffix in {".txt", ".md"}:
        try:
            pages = [Page(1, _clean(data.decode("utf-8-sig")), "text")]
        except UnicodeDecodeError as exc:
            raise DocumentError("text files must be UTF-8") from exc
    elif suffix == ".docx":
        pages = [Page(1, _clean(_docx(data)), "docx")]
    elif suffix == ".pdf":
        pages = await _pdf(data, ocr)
    elif suffix in IMAGE_TYPES:
        if ocr is None:
            pages = [Page(1, "", "unreadable")]
        else:
            pages = [Page(1, _clean(await ocr.read(data, IMAGE_TYPES[suffix])), "ocr")]
    else:
        raise DocumentError("supported: .pdf, .docx, .txt, .png, .jpg")
    return Document(filename=PurePath(filename).name[:200], pages=tuple(pages))
