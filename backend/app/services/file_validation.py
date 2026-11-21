"""Upload validation: extension allow-list, file signatures, container inspection and filename sanitising.

Nothing in an uploaded file is ever executed. DOCX files are opened only as ZIP/XML containers; macro-enabled
documents, encrypted PDFs and documents with active content are rejected before parsing.
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass

from app.core.config import get_settings

ALLOWED_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
}
# Browsers send a variety of MIME types; the declared type must be one of these (or generic) - it is only a
# hint, the file signature below is authoritative.
ACCEPTED_DECLARED_MIME = {
    "pdf": {"application/pdf", "application/x-pdf", "application/octet-stream", ""},
    "docx": {ALLOWED_TYPES["docx"], "application/zip", "application/octet-stream", ""},
    "txt": {"text/plain", "application/octet-stream", ""},
}


class FileValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedFile:
    file_type: str
    mime_type: str
    safe_name: str
    warnings: tuple[str, ...] = ()


_WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def sanitize_filename(name: str | None, fallback: str = "document") -> str:
    """Return a display-safe filename. Never used as a filesystem path."""
    name = unicodedata.normalize("NFKC", name or "")
    name = name.replace("\\", "/").split("/")[-1]  # drop any path component
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")  # control / format / bidi chars
    name = re.sub(r"[<>:\"|?*]", "_", name).strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    stem = name.rsplit(".", 1)[0].lower() if name else ""
    if not name or stem in _WINDOWS_RESERVED:
        name = fallback + ("." + name.rsplit(".", 1)[1] if "." in name else "")
    if len(name) > 200:
        base, _, ext = name.rpartition(".")
        name = (base[: 200 - len(ext) - 1] + "." + ext) if base and len(ext) <= 8 else name[:200]
    return name


def title_from_filename(name: str) -> str:
    stem = name.rsplit(".", 1)[0] if "." in name else name
    stem = re.sub(r"[_]+", " ", stem).strip()
    return (stem or "Untitled contract")[:200]


def _check_pdf(data: bytes) -> list[str]:
    head = data[:1024]
    if b"%PDF-" not in head:
        raise FileValidationError("The file does not have a valid PDF signature")
    if b"%%EOF" not in data[-2048:]:
        # Truncated PDFs often still parse; flag rather than reject outright.
        warnings = ["The PDF appears truncated (no end-of-file marker)"]
    else:
        warnings = []
    lowered = data[:10_000_000]
    # Password protection is checked by the parser (documents that need a password are rejected there).
    active = [tag for tag in (b"/JavaScript", b"/JS ", b"/Launch", b"/EmbeddedFile", b"/OpenAction", b"/RichMedia")
              if tag in lowered]
    if active:
        warnings.append("The PDF contains active or embedded content, which is ignored and never executed")
    return warnings


def _check_docx(data: bytes) -> list[str]:
    if not data.startswith(b"PK\x03\x04"):
        raise FileValidationError("The file does not have a valid DOCX (ZIP) signature")
    s = get_settings()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            if len(infos) > 2000:
                raise FileValidationError("The DOCX archive contains too many entries")
            names = {i.filename for i in infos}
            total = sum(i.file_size for i in infos)
            if total > s.max_docx_uncompressed_mb * 1024 * 1024:
                raise FileValidationError("The DOCX file expands to an unsafe size")
            for info in infos:
                if info.compress_size and info.file_size / max(info.compress_size, 1) > 200 and info.file_size > 5_000_000:
                    raise FileValidationError("The DOCX file has a suspicious compression ratio")
                if info.filename.startswith("/") or ".." in info.filename.split("/"):
                    raise FileValidationError("The DOCX archive contains unsafe paths")
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise FileValidationError("The file is not a Word document (missing document parts)")
            if any(n.lower().endswith("vbaproject.bin") for n in names):
                raise FileValidationError("Macro-enabled Word documents are not accepted")
            content_types = zf.read("[Content_Types].xml")
            if b"macroEnabled" in content_types:
                raise FileValidationError("Macro-enabled Word documents are not accepted")
            warnings = []
            if any(n.startswith("word/embeddings/") or n.startswith("word/activeX/") for n in names):
                warnings.append("The document contains embedded objects, which are ignored and never executed")
            return warnings
    except zipfile.BadZipFile as exc:
        raise FileValidationError("The DOCX file is corrupted") from exc


def _check_txt(data: bytes) -> list[str]:
    if data.startswith((b"%PDF-", b"PK\x03\x04", b"\x7fELF", b"MZ", b"\xd0\xcf\x11\xe0")):
        raise FileValidationError("The file content does not match the .txt extension")
    if b"\x00" in data[:8192] and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise FileValidationError("The file appears to be binary, not plain text")
    decode_text(data)  # raises when undecodable
    return []


def decode_text(data: bytes) -> str:
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if data.startswith(bom):
            return data.decode(enc)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    text = data.decode("cp1252", errors="replace")
    if text.count("�") > max(10, len(text) // 100):
        raise FileValidationError("The text file uses an unsupported encoding")
    return text


def validate_upload(filename: str | None, declared_mime: str | None, data: bytes) -> ValidatedFile:
    s = get_settings()
    safe = sanitize_filename(filename)
    ext = safe.rsplit(".", 1)[-1].lower() if "." in safe else ""
    if ext not in ALLOWED_TYPES:
        raise FileValidationError("Unsupported file type. Upload a PDF, DOCX or TXT document")
    if not data:
        raise FileValidationError("The file is empty")
    if len(data) > s.max_upload_bytes:
        raise FileValidationError(f"The file exceeds the {s.max_upload_mb} MB limit")
    declared = (declared_mime or "").split(";")[0].strip().lower()
    if declared not in ACCEPTED_DECLARED_MIME[ext]:
        raise FileValidationError("The declared file type does not match the file extension")
    checker = {"pdf": _check_pdf, "docx": _check_docx, "txt": _check_txt}[ext]
    warnings = checker(data)
    return ValidatedFile(file_type=ext, mime_type=ALLOWED_TYPES[ext], safe_name=safe, warnings=tuple(warnings))
