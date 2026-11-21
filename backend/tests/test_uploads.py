"""Upload validation and document processing security."""

from __future__ import annotations

import io
import zipfile

import pytest

from app.services.file_validation import FileValidationError, sanitize_filename, validate_upload
from tests.conftest import contract_text, make_docx, make_pdf, run_jobs


def test_upload_txt_pdf_docx_and_analyse(client, alice):
    txt = alice.upload("agreement.txt", contract_text().encode(), "text/plain")
    text = contract_text()
    cut = text.index("6. Limitation of Liability")
    pdf = alice.upload("agreement.pdf", make_pdf([text[:cut], text[cut:]]), "application/pdf")
    docx_bytes = make_docx(contract_text().split("\n"), page_break_after=10, table=[["Fee", "USD 12,500"]])
    docx = alice.upload("agreement.docx", docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    for r in (txt, pdf, docx):
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "queued"
    assert run_jobs() == 3
    for r in (txt, pdf, docx):
        c = alice.get(alice.ws(f"/contracts/{r.json()['id']}")).json()
        assert c["status"] == "analyzed", c
        assert c["risk_level"] in ("critical", "high")
    pdf_doc = alice.get(alice.ws(f"/contracts/{pdf.json()['id']}")).json()["document"]
    assert pdf_doc["page_count"] == 2 and pdf_doc["has_page_numbers"]
    findings = alice.get(alice.ws(f"/contracts/{pdf.json()['id']}/findings")).json()
    assert any(f["page_number"] == 2 for f in findings)  # references point at the right page
    docx_doc = alice.get(alice.ws(f"/contracts/{docx.json()['id']}")).json()["document"]
    assert docx_doc["page_count"] == 2


@pytest.mark.parametrize("name,data,mime,msg", [
    ("contract.exe", b"MZ\x90\x00", "application/octet-stream", "Unsupported file type"),
    ("contract.html", b"<script>alert(1)</script>", "text/html", "Unsupported file type"),
    ("contract.pdf", b"PK\x03\x04 not a pdf", "application/pdf", "PDF signature"),
    ("contract.docx", b"%PDF-1.7 pretending", "application/octet-stream", "DOCX (ZIP) signature"),
    ("contract.txt", b"%PDF-1.4\n...", "text/plain", "does not match"),
    ("contract.txt", b"MZ\x90\x00\x03\x00\x00\x00", "text/plain", "does not match"),
    ("contract.txt", b"abc\x00\x00\x00binary", "text/plain", "binary"),
    ("contract.pdf", b"", "application/pdf", "empty"),
    ("contract.pdf", b"%PDF-1.4", "text/html", "declared file type"),
    ("noextension", b"hello", "text/plain", "Unsupported file type"),
])
def test_rejected_uploads(client, alice, name, data, mime, msg):
    r = alice.upload(name, data, mime)
    assert r.status_code == 422
    assert msg.lower() in r.json()["detail"].lower()
    assert alice.get(alice.ws("/contracts")).json()["total"] == 0


def test_oversized_upload(client, alice):
    big = b"A" * (2 * 1024 * 1024 + 10)
    r = alice.upload("big.txt", big, "text/plain")
    assert r.status_code in (413, 422)


def _docx_with(extra: dict[str, bytes], content_types: bytes | None = None) -> bytes:
    base = make_docx(["Hello world. This is a test contract."])
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(base)) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "[Content_Types].xml" and content_types is not None:
                data = content_types
            dst.writestr(item, data)
        for name, data in extra.items():
            dst.writestr(name, data)
    return out.getvalue()


def test_macro_docx_rejected():
    data = _docx_with({"word/vbaProject.bin": b"\x00" * 100})
    with pytest.raises(FileValidationError, match="Macro"):
        validate_upload("evil.docx", "", data)


def test_docx_bomb_rejected():
    data = _docx_with({"word/media/bomb.bin": b"\x00" * (120 * 1024 * 1024)})
    with pytest.raises(FileValidationError, match=r"unsafe size|compression"):
        validate_upload("bomb.docx", "", data)


def test_docx_embedded_objects_flagged_not_executed():
    data = _docx_with({"word/embeddings/oleObject1.bin": b"\xd0\xcf\x11\xe0payload"})
    v = validate_upload("ole.docx", "", data)
    assert any("embedded" in w for w in v.warnings)


def test_fake_docx_zip_rejected():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("readme.txt", "not a word file")
    with pytest.raises(FileValidationError, match="not a Word document"):
        validate_upload("fake.docx", "", out.getvalue())


def test_pdf_with_javascript_flagged():
    pdf = make_pdf(["Simple contract text with enough words to extract."]).replace(b"/Type/Catalog", b"/Type/Catalog/OpenAction<</S/JavaScript/JS(app.alert(1))>>", 1)
    v = validate_upload("js.pdf", "application/pdf", pdf)
    assert any("active" in w for w in v.warnings)


@pytest.mark.parametrize("raw,expected", [
    ("../../etc/passwd.txt", "passwd.txt"),
    ("..\\..\\windows\\system32\\evil.pdf", "evil.pdf"),
    ("con.txt", "document.txt"),
    ("inv‮oice\u0000.pdf", "invoice.pdf"),
    ('a<b>c:"d"|e?.pdf', "a_b_c__d__e_.pdf"),
    ("", "document"),
])
def test_filename_sanitising(raw, expected):
    assert sanitize_filename(raw) == expected


def test_malicious_filename_upload_is_neutralised(client, alice):
    r = alice.upload("../../<script>alert(1)</script>.txt", contract_text().encode(), "text/plain")
    assert r.status_code == 201
    doc = r.json()["document"]
    assert "/" not in doc["original_filename"] and ".." not in doc["original_filename"]


def test_invalid_pdf_marks_contract_failed_without_partial_data(client, alice, db):
    from sqlalchemy import func, select

    from app.models import AnalysisResult, ExtractedClause

    r = alice.upload("broken.pdf", b"%PDF-1.4\n%garbage that is not a pdf\n%%EOF", "application/pdf")
    assert r.status_code == 201
    run_jobs()
    c = alice.get(alice.ws(f"/contracts/{r.json()['id']}")).json()
    assert c["status"] == "failed" and c["error_message"]
    assert db.scalar(select(func.count()).select_from(AnalysisResult)) == 0
    assert db.scalar(select(func.count()).select_from(ExtractedClause)) == 0


def test_empty_text_document_fails_cleanly(client, alice):
    r = alice.upload("blank.pdf", make_pdf([""]), "application/pdf")
    run_jobs()
    c = alice.get(alice.ws(f"/contracts/{r.json()['id']}")).json()
    assert c["status"] == "failed"
    assert "no text" in c["error_message"].lower()


def test_page_limit_enforced(client, alice):
    r = alice.upload("long.pdf", make_pdf(["page"] * 51), "application/pdf")
    run_jobs()
    c = alice.get(alice.ws(f"/contracts/{r.json()['id']}")).json()
    assert c["status"] == "failed" and "limit" in c["error_message"]


def test_rename_search_filter_delete(client, alice):
    a = alice.upload("first.txt", contract_text().encode(), "text/plain").json()
    alice.upload("nda.txt", contract_text("mutual_nda.txt").encode(), "text/plain")
    run_jobs()
    assert alice.patch(alice.ws(f"/contracts/{a['id']}"), json={"title": "  Renamed   MSA "}).json()["title"] == "Renamed MSA"
    assert alice.get(alice.ws("/contracts?q=renamed")).json()["total"] == 1
    assert alice.get(alice.ws("/contracts?q=Harbor&search_content=true")).json()["total"] == 1
    assert alice.get(alice.ws("/contracts?q=Harbor")).json()["total"] == 0
    assert alice.get(alice.ws("/contracts?q=%25")).json()["total"] == 2  # wildcard characters are not injected
    assert alice.get(alice.ws("/contracts?q=' OR 1=1 --")).json()["total"] == 0
    high = alice.get(alice.ws("/contracts?risk=critical&risk=high")).json()
    assert high["total"] == 1 and high["items"][0]["id"] == a["id"]
    assert alice.get(alice.ws("/contracts?status=analyzed")).json()["total"] == 2
    assert alice.get(alice.ws("/contracts?date_from=2000-01-01&date_to=2000-12-31")).json()["total"] == 0
    assert alice.get(alice.ws("/contracts?sort=risk")).json()["items"][0]["id"] == a["id"]
    assert alice.delete(alice.ws(f"/contracts/{a['id']}")).status_code == 204
    assert alice.get(alice.ws(f"/contracts/{a['id']}")).status_code == 404
    assert alice.get(alice.ws("/contracts")).json()["total"] == 1


def test_delete_removes_stored_files(client, alice):
    from pathlib import Path

    from app.core.config import get_settings

    r = alice.upload("todelete.txt", contract_text().encode(), "text/plain").json()
    run_jobs()
    alice.post(alice.ws("/reports"), json={"contract_id": r["id"], "format": "pdf"})
    root = Path(get_settings().local_storage_path)
    before = [p for p in root.rglob("*") if p.is_file()]
    assert len(before) >= 2
    alice.delete(alice.ws(f"/contracts/{r['id']}"))
    after = [p for p in root.rglob("*") if p.is_file()]
    assert len(after) == len(before) - 2
