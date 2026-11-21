"""Text extraction (PDF / DOCX / TXT), clause segmentation and retrieval chunking.

Extraction can run in an isolated child process with a hard timeout (``extract_isolated``) so that a hostile or
pathological document cannot block or exhaust the worker.
"""

from __future__ import annotations

import io
import logging
import multiprocessing
import re
import shutil
import unicodedata
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from typing import Any

from app.core.config import get_settings
from app.services.file_validation import FileValidationError, decode_text

log = logging.getLogger(__name__)


class ExtractionError(RuntimeError):
    pass


@dataclass
class PageText:
    number: int | None
    text: str


@dataclass
class ExtractedDocument:
    pages: list[PageText]
    page_count: int | None
    has_page_numbers: bool
    method: str
    warnings: list[str] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        return "\n\n".join(p.text for p in self.pages)


@dataclass
class Clause:
    position: int
    section_ref: str | None
    heading: str | None
    text: str
    page_number: int | None
    category: str = "general"
    parent_heading: str | None = None


@dataclass
class Chunk:
    index: int
    text: str
    page_number: int | None
    section_ref: str | None


# --- Normalisation --------------------------------------------------------------------------------------------

_INVISIBLE = dict.fromkeys(
    [0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x00AD, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067,
     0x2068, 0x2069, 0x061C, 0x200E, 0x200F]
)
_SPACES = {0x00A0: " ", 0x2007: " ", 0x202F: " ", 0x2009: " ", 0x200A: " ", 0x3000: " "}


def normalise_text(text: str) -> str:
    """Remove invisible/bidi control characters (which can hide content), normalise spaces and line endings."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").translate(_INVISIBLE).translate(_SPACES)
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch) != "Cc")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # de-hyphenate line breaks
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --- Format specific extraction -------------------------------------------------------------------------------

def ocr_available() -> bool:
    return get_settings().ocr_enabled and shutil.which("tesseract") is not None


def _extract_pdf(data: bytes) -> ExtractedDocument:
    import pymupdf

    s = get_settings()
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ExtractionError("The PDF could not be opened (it may be corrupted)") from exc
    with doc:
        if doc.needs_pass:
            raise ExtractionError("Password-protected PDFs are not supported")
        if doc.page_count == 0:
            raise ExtractionError("The PDF has no pages")
        if doc.page_count > s.max_pdf_pages:
            raise ExtractionError(f"The PDF has {doc.page_count} pages; the limit is {s.max_pdf_pages}")
        pages: list[PageText] = []
        warnings: list[str] = []
        ocr_pages = 0
        can_ocr = ocr_available()
        total = 0
        for i in range(1, doc.page_count + 1):
            page: Any = doc[i - 1]
            text = page.get_text("text", sort=True) or ""
            if len(text.strip()) < 20 and can_ocr and ocr_pages < s.ocr_max_pages:
                try:
                    tp = page.get_textpage_ocr(full=True, dpi=200)
                    text = page.get_text("text", textpage=tp, sort=True) or ""
                    ocr_pages += 1
                except Exception:  # noqa: BLE001 - OCR is optional and best effort
                    warnings.append(f"OCR failed on page {i}")
            text = normalise_text(text)
            total += len(text)
            if total > s.max_text_chars:
                raise ExtractionError("The document contains more text than the configured limit")
            pages.append(PageText(i, text))
        empty = sum(1 for p in pages if not p.text)
        if empty and not can_ocr:
            warnings.append(
                f"{empty} page(s) contain no extractable text (likely scanned). OCR is not available on this server"
            )
        method = "ocr" if ocr_pages == len(pages) else ("mixed" if ocr_pages else "text")
        return ExtractedDocument(pages, doc.page_count, True, method, warnings)


_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _extract_docx(data: bytes) -> ExtractedDocument:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise ExtractionError("The DOCX file could not be read (it may be corrupted)") from exc

    pages: list[list[str]] = [[]]
    saw_break = False
    body = document.element.body
    for child in body.iterchildren():
        if child.tag == _W_NS + "p":
            para = Paragraph(child, document)
            for br in child.iter(_W_NS + "br"):
                if br.get(_W_NS + "type") == "page":
                    saw_break = True
            if para.text.strip():
                pages[-1].append(para.text)
            if any(br.get(_W_NS + "type") == "page" for br in child.iter(_W_NS + "br")):
                pages.append([])
        elif child.tag == _W_NS + "tbl":
            table = Table(child, document)
            for row in table.rows:
                cells: list[str] = []
                for cell in row.cells:
                    t = cell.text.strip()
                    if t and (not cells or cells[-1] != t):  # merged cells repeat their text
                        cells.append(t)
                if cells:
                    pages[-1].append(" | ".join(cells))
    texts = [normalise_text("\n".join(p)) for p in pages]
    texts = [t for t in texts if t] or [""]
    if sum(len(t) for t in texts) > get_settings().max_text_chars:
        raise ExtractionError("The document contains more text than the configured limit")
    if saw_break and len(texts) > 1:
        return ExtractedDocument([PageText(i, t) for i, t in enumerate(texts, 1)], len(texts), True, "text",
                                 ["Page numbers are based on explicit page breaks in the Word document"])
    return ExtractedDocument([PageText(None, "\n\n".join(texts))], None, False, "text",
                             ["Word documents have no fixed pages; references use clause numbers instead"])


def _extract_txt(data: bytes) -> ExtractedDocument:
    try:
        text = decode_text(data)
    except FileValidationError as exc:
        raise ExtractionError(str(exc)) from exc
    if len(text) > get_settings().max_text_chars:
        raise ExtractionError("The document contains more text than the configured limit")
    parts = text.split("\f")
    if len(parts) > 1:
        pages = [PageText(i, normalise_text(t)) for i, t in enumerate(parts, 1)]
        return ExtractedDocument(pages, len(pages), True, "text")
    return ExtractedDocument([PageText(None, normalise_text(text))], None, False, "text")


def extract(data: bytes, file_type: str) -> ExtractedDocument:
    fn = {"pdf": _extract_pdf, "docx": _extract_docx, "txt": _extract_txt}.get(file_type)
    if fn is None:
        raise ExtractionError("Unsupported file type")
    result = fn(data)
    if not result.full_text.strip():
        raise ExtractionError(
            "No text could be extracted from the document. If it is a scanned document, OCR must be enabled "
            "on the server (Tesseract)."
        )
    return result


def extract_isolated(data: bytes, file_type: str, timeout: int) -> ExtractedDocument:
    """Run extraction in a separate process with a hard timeout."""
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=1, mp_context=ctx) as pool:
        future = pool.submit(extract, data, file_type)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeout as exc:
            for proc in list(getattr(pool, "_processes", {}).values()):
                proc.terminate()
            raise ExtractionError(f"Document processing exceeded the {timeout}s time limit") from exc


# --- Clause segmentation --------------------------------------------------------------------------------------

_HEADING_PATTERNS = [
    # "Section 4", "ARTICLE IV - TERM", "Clause 12.1 Confidentiality"
    re.compile(r"^(?:section|article|clause)\s+(\d{1,3}(?:\.\d{1,3}){0,3}|[IVXLC]{1,6})\b[.:)\-–—]?\s*(.{0,160})$", re.I),
    # "4. Payment", "4) Payment" - top-level numbers need a delimiter and a capitalised heading/body
    re.compile(r"^(\d{1,2})[.)]\s+([A-Z(\"“].{0,2000})$"),
    # "4.2 The Client shall...", "4.2.1. Fees" - sub-clause numbers
    re.compile(r"^(\d{1,2}(?:\.\d{1,2}){1,3})\.?\s+([A-Z(\"“].{0,2000})$"),
]
_CAPS_HEADING = re.compile(r"^[A-Z][A-Z0-9 &,'’/\-–()]{3,80}$")


def _split_heading(rest: str) -> tuple[str | None, str]:
    """Split 'Confidentiality. Each party shall...' into heading and body."""
    rest = rest.strip()
    if not rest:
        return None, ""
    m = re.match(r"^([A-Z][A-Za-z0-9 ,&'’/\-–()]{2,80}?)(?:[.:]\s+|\s+[-–—]\s+)(.*)$", rest, re.S)
    if m and len(m.group(1).split()) <= 8:
        return m.group(1).strip(), m.group(2).strip()
    if len(rest.split()) <= 8 and not rest.endswith((".", ";", ",")):
        return rest, ""
    return None, rest


def segment_clauses(doc: ExtractedDocument) -> list[Clause]:
    lines: list[tuple[str, int | None]] = []
    for page in doc.pages:
        for line in page.text.split("\n"):
            if line.strip():
                lines.append((line.strip(), page.number))

    clauses: list[Clause] = []
    current: dict | None = None

    def flush() -> None:
        nonlocal current
        if current and (current["body"] or current["heading"]):
            text = "\n".join(([current["heading_line"]] if current["heading_line"] else []) + current["body"]).strip()
            if text:
                clauses.append(Clause(len(clauses), current["ref"], current["heading"], text, current["page"]))
        current = None

    for line, line_page in lines:
        ref = heading = None
        body_text = ""
        is_heading = False
        for pat in _HEADING_PATTERNS:
            m = pat.match(line)
            if m:
                ref = m.group(1)
                heading, body_text = _split_heading(m.group(2))
                is_heading = True
                break
        if not is_heading and _CAPS_HEADING.match(line) and len(line.split()) <= 8:
            heading, is_heading = line.title(), True
        if is_heading:
            flush()
            current = {"ref": ref, "heading": heading, "heading_line": line if not body_text else None,
                       "body": [line] if body_text else [], "page": line_page}
        else:
            if current is None:
                current = {"ref": None, "heading": None, "heading_line": None, "body": [], "page": line_page}
            current["body"].append(line)
    flush()

    if len(clauses) <= 1:
        # No structure detected: fall back to paragraph groups of ~1500 characters.
        clauses = []
        for page in doc.pages:
            buf: list[str] = []
            for para in re.split(r"\n\s*\n", page.text):
                buf.append(para.strip())
                if sum(len(b) for b in buf) > 1500:
                    clauses.append(Clause(len(clauses), None, None, "\n\n".join(buf), page.number))
                    buf = []
            if buf and any(buf):
                clauses.append(Clause(len(clauses), None, None, "\n\n".join(buf), page.number))

    # Inherit the parent heading for sub-clauses ("4.2" under "4. Payment") so they keep context.
    headings: dict[str, str] = {}
    for c in clauses:
        if c.section_ref and c.heading:
            headings[c.section_ref] = c.heading
        elif c.section_ref and "." in c.section_ref:
            parent = c.section_ref.split(".")[0]
            if parent in headings:
                c.parent_heading = headings[parent]
    return clauses


def section_label(ref: str | None, heading: str | None) -> str | None:
    if ref and heading:
        return f"{ref} {heading}"[:120]
    return (ref or heading or None) and (ref or heading or "")[:120]


def build_chunks(clauses: list[Clause], target: int = 1200, overlap: int = 150) -> list[Chunk]:
    chunks: list[Chunk] = []
    buf: list[Clause] = []

    def emit(text: str, first: Clause) -> None:
        chunks.append(Chunk(len(chunks), text.strip(), first.page_number, section_label(first.section_ref, first.heading)))

    for clause in clauses:
        if len(clause.text) > target * 1.5:
            if buf:
                emit("\n".join(c.text for c in buf), buf[0])
                buf = []
            start = 0
            while start < len(clause.text):
                end = min(len(clause.text), start + target)
                if end < len(clause.text):
                    cut = clause.text.rfind(". ", start + target // 2, end)
                    end = cut + 1 if cut > 0 else end
                emit(clause.text[start:end], clause)
                if end >= len(clause.text):
                    break
                start = max(end - overlap, start + 1)
            continue
        if buf and (sum(len(c.text) for c in buf) + len(clause.text) > target or clause.page_number != buf[0].page_number):
            emit("\n".join(c.text for c in buf), buf[0])
            buf = []
        buf.append(clause)
    if buf:
        emit("\n".join(c.text for c in buf), buf[0])
    return [c for c in chunks if c.text]
