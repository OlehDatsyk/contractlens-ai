"""End-to-end smoke test against a running ContractLens AI server, using synthetic documents only.

Usage (from the repository root, with the backend virtualenv):
    backend/.venv/Scripts/python scripts/smoke_test.py http://localhost:8000          # Windows
    backend/.venv/bin/python scripts/smoke_test.py https://your-app.onrender.com      # macOS / Linux

It registers two throw-away accounts, uploads a synthetic PDF and DOCX, waits for analysis, asks the assistant a
question, compares the contracts, creates and downloads a report, creates a deadline, checks that a second user
cannot access the first user's data, and finally deletes what it created. It never sends real documents and does
not trigger paid AI calls unless the server itself is configured with an AI provider.
"""

from __future__ import annotations

import io
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "backend" / "tests" / "fixtures"
PASSWORD = "Smoke-Test-Pass-" + uuid.uuid4().hex[:6] + "!"
CSRF = {"X-Requested-With": "ContractLens"}
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail else ''}")


def make_pdf(text: str) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    cut = text.index("6. Limitation")
    for part in (text[:cut], text[cut:]):
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(50, 50, 550, 800), part, fontsize=9)
    data = doc.tobytes()
    doc.close()
    return data


def make_docx(text: str) -> bytes:
    import docx

    d = docx.Document()
    for line in text.split("\n"):
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


class User:
    def __init__(self, base: str, label: str) -> None:
        self.c = httpx.Client(base_url=base, timeout=60, follow_redirects=False)
        self.email = f"smoke-{label}-{uuid.uuid4().hex[:8]}@example.com"
        self.h: dict[str, str] = {}
        self.ws = ""

    def register(self) -> httpx.Response:
        r = self.c.post("/api/auth/register", json={"email": self.email, "password": PASSWORD, "full_name": "Smoke Test"})
        if r.status_code == 201:
            self.h = {"Authorization": f"Bearer {r.json()['access_token']}"}
            self.ws = self.c.get("/api/workspaces", headers=self.h).json()[0]["id"]
        return r

    def login(self) -> httpx.Response:
        r = self.c.post("/api/auth/login", json={"email": self.email, "password": PASSWORD})
        if r.status_code == 200:
            self.h = {"Authorization": f"Bearer {r.json()['access_token']}"}
        return r

    def req(self, method: str, path: str, **kw) -> httpx.Response:
        return self.c.request(method, path, headers=self.h, **kw)


def wait_analyzed(u: User, cid: str, timeout: float = 240) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        c = u.req("GET", f"/api/workspaces/{u.ws}/contracts/{cid}").json()
        if c["status"] in ("analyzed", "failed"):
            return c
        time.sleep(2)
    return c


def main(base: str) -> int:
    base = base.rstrip("/")
    a, b = User(base, "a"), User(base, "b")
    r = httpx.get(base + "/health/ready", timeout=60)
    check("health/ready", r.status_code == 200, r.text[:200])

    check("1. register", a.register().status_code == 201)
    login = a.login()
    check("2. login", login.status_code == 200)
    cookie = login.headers.get("set-cookie", "")
    check("   refresh cookie is HttpOnly + SameSite=Strict", "httponly" in cookie.lower() and "samesite=strict" in cookie.lower())
    if base.startswith("http://") and "secure" in cookie.lower():
        check("   refresh token rotation", True, "skipped: Secure cookies are only sent over HTTPS")
    else:
        check("   refresh token rotation", a.c.post("/api/auth/refresh", headers=CSRF).status_code == 200)
    r = a.req("POST", "/api/workspaces", json={"name": "Smoke test workspace"})
    check("3. create workspace", r.status_code == 201)
    a.ws = r.json()["id"]
    caps = a.req("GET", f"/api/workspaces/{a.ws}/capabilities").json()
    check("   AI status reported", "available" in caps["ai"], f"ai available={caps['ai']['available']} ({caps['ai'].get('reason') or caps['ai'].get('provider')})")

    text_a = (FIXTURES / "services_agreement_a.txt").read_text(encoding="utf-8")
    text_b = (FIXTURES / "services_agreement_b.txt").read_text(encoding="utf-8")
    r = a.req("POST", f"/api/workspaces/{a.ws}/contracts", files={"file": ("smoke-msa-v1.pdf", make_pdf(text_a), "application/pdf")})
    check("4. upload PDF", r.status_code == 201, r.text[:150] if r.status_code != 201 else "")
    pdf_id = r.json()["id"]
    docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    r = a.req("POST", f"/api/workspaces/{a.ws}/contracts", files={"file": ("smoke-msa-v2.docx", make_docx(text_b), docx_mime)})
    check("5. upload DOCX", r.status_code == 201, r.text[:150] if r.status_code != 201 else "")
    docx_id = r.json()["id"]
    bad = a.req("POST", f"/api/workspaces/{a.ws}/contracts", files={"file": ("evil.pdf", b"MZ\x90\x00 not a pdf", "application/pdf")})
    check("   spoofed file rejected", bad.status_code == 422)

    c1, c2 = wait_analyzed(a, pdf_id), wait_analyzed(a, docx_id)
    check("6. analyse contracts", c1["status"] == "analyzed" and c2["status"] == "analyzed", f"{c1['status']}/{c2['status']} risk={c1.get('risk_level')}/{c2.get('risk_level')}")
    an = a.req("GET", f"/api/workspaces/{a.ws}/contracts/{pdf_id}/analysis").json()
    check("7. extracted information", bool(an and an["parties"] and an["effective_date"]), f"engine={an and an['engine']} parties={len(an['parties']) if an else 0}")
    findings = a.req("GET", f"/api/workspaces/{a.ws}/contracts/{pdf_id}/findings").json()
    check("8. risk findings", len(findings) > 0 and any(f["page_number"] for f in findings), f"{len(findings)} findings")

    conv = a.req("POST", f"/api/workspaces/{a.ws}/contracts/{pdf_id}/conversations", json={}).json()
    r = a.req("POST", f"/api/workspaces/{a.ws}/contracts/{pdf_id}/conversations/{conv['id']}/messages",
              json={"content": "What happens if payment is late?"})
    reply = r.json()[1] if r.status_code == 201 else {}
    check("9. assistant question", r.status_code == 201 and bool(reply.get("citations")), f"mode={reply.get('mode')} citations={len(reply.get('citations', []))}")

    r = a.req("GET", f"/api/workspaces/{a.ws}/compare", params={"a": pdf_id, "b": docx_id})
    cmp = r.json() if r.status_code == 200 else {}
    check("10. compare contracts", r.status_code == 200 and bool(cmp.get("clauses", {}).get("modified")),
          f"modified={len(cmp.get('clauses', {}).get('modified', []))} added={len(cmp.get('clauses', {}).get('added', []))}")

    r = a.req("POST", f"/api/workspaces/{a.ws}/reports", json={"contract_id": pdf_id, "format": "pdf"})
    check("11. create report", r.status_code == 201)
    link = a.req("POST", f"/api/workspaces/{a.ws}/reports/{r.json()['id']}/download-link").json()["url"]
    dl = a.c.get(link)
    check("12. export report", dl.status_code == 200 and dl.content.startswith(b"%PDF"), f"{len(dl.content)} bytes")

    r = a.req("POST", f"/api/workspaces/{a.ws}/deadlines", json={"title": "Smoke test deadline", "due_date": "2030-01-15", "contract_id": pdf_id})
    check("13. create deadline", r.status_code == 201)

    doc_link = a.req("POST", f"/api/workspaces/{a.ws}/contracts/{pdf_id}/download-link").json()["url"]
    check("   authorised document download", a.c.get(doc_link).content.startswith(b"%PDF"))
    check("15. permissions: second user", b.register().status_code == 201)
    blocked = [
        b.req("GET", f"/api/workspaces/{a.ws}/contracts/{pdf_id}").status_code,
        b.req("GET", f"/api/workspaces/{b.ws}/contracts/{pdf_id}").status_code,
        b.req("GET", f"/api/workspaces/{a.ws}/contracts/{pdf_id}/analysis").status_code,
        b.req("POST", f"/api/workspaces/{b.ws}/contracts/{pdf_id}/download-link").status_code,
        b.req("GET", f"/api/workspaces/{b.ws}/compare", params={"a": pdf_id, "b": docx_id}).status_code,
    ]
    check("   cross-workspace access blocked", all(s == 404 for s in blocked), str(blocked))
    tampered = doc_link[:-3] + ("AAA" if not doc_link.endswith("AAA") else "BBB")
    check("   tampered download link rejected", a.c.get(tampered).status_code == 404)
    check("   unauthenticated API rejected", httpx.get(f"{base}/api/workspaces/{a.ws}/contracts", timeout=30).status_code == 401)

    check("14. delete contract", a.req("DELETE", f"/api/workspaces/{a.ws}/contracts/{pdf_id}").status_code == 204)
    check("   deleted document link no longer works", a.c.get(doc_link).status_code == 404)
    a.req("DELETE", f"/api/workspaces/{a.ws}/contracts/{docx_id}")
    a.req("DELETE", f"/api/workspaces/{a.ws}", json={"confirm_name": "Smoke test workspace"})

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"))
