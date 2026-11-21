"""Test fixtures. Uses SQLite by default (TEST_DATABASE_URL may point to PostgreSQL, as in CI).

No real AI providers, email servers or credentials are used: provider calls are mocked.
"""

from __future__ import annotations

import io
import os
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="contractlens-tests-"))
os.environ.update({
    "ENVIRONMENT": "test",
    "DATABASE_URL": os.environ.get("TEST_DATABASE_URL", f"sqlite:///{(_TMP / 'test.db').as_posix()}"),
    "STORAGE_BACKEND": "local",
    "LOCAL_STORAGE_PATH": str(_TMP / "storage"),
    "JWT_SECRET_KEY": "test-secret-key-for-automated-tests-only-0123456789",
    "AI_PROVIDER": "none",
    "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "", "GEMINI_API_KEY": "",
    "SMTP_HOST": "",
    "RATE_LIMIT_ENABLED": "false",
    "RUN_WORKER_IN_API": "false",
    "FRONTEND_URL": "http://testserver",
    "MAX_UPLOAD_MB": "2",
    "MAX_PDF_PAGES": "50",
})

import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from alembic import command  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.core.rate_limit import limiter  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
PASSWORD = "Sup3r-Secure-Pass!"


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    cfg = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    cfg.attributes["database_url"] = get_settings().database_url
    cfg.attributes["configure_logger"] = False
    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    command.upgrade(cfg, "head")  # exercises the real migrations
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    yield
    limiter.reset()
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


@pytest.fixture
def client() -> Iterator[TestClient]:
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


class Account:
    def __init__(self, client: TestClient, email: str, token: str) -> None:
        self.client, self.email, self.token = client, email, token
        self.headers = {"Authorization": f"Bearer {token}"}
        self.workspace_id = client.get("/api/workspaces", headers=self.headers).json()[0]["id"]

    def get(self, url: str, **kw):
        return self.client.get(url, headers=self.headers, **kw)

    def post(self, url: str, **kw):
        return self.client.post(url, headers=self.headers, **kw)

    def patch(self, url: str, **kw):
        return self.client.patch(url, headers=self.headers, **kw)

    def delete(self, url: str, **kw):
        return self.client.request("DELETE", url, headers=self.headers, **kw)

    def ws(self, path: str = "") -> str:
        return f"/api/workspaces/{self.workspace_id}{path}"

    def upload(self, name: str, data: bytes, mime: str = "application/octet-stream", analyze: bool = True, workspace_id: str | None = None):
        wid = workspace_id or self.workspace_id
        return self.client.post(f"/api/workspaces/{wid}/contracts", headers=self.headers,
                                files={"file": (name, io.BytesIO(data), mime)}, data={"analyze": str(analyze).lower()})


def register(client: TestClient, email: str | None = None, name: str = "Test User") -> Account:
    email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/auth/register", json={"email": email, "password": PASSWORD, "full_name": name})
    assert r.status_code == 201, r.text
    return Account(client, email, r.json()["access_token"])


@pytest.fixture
def alice(client: TestClient) -> Account:
    return register(client, "alice@example.com", "Alice Owner")


@pytest.fixture
def bob(client: TestClient) -> Account:
    return register(client, "bob@example.com", "Bob Other")


def run_jobs(max_jobs: int = 20) -> int:
    from app.worker.runner import run_once

    n = 0
    while n < max_jobs and run_once(isolate=False):
        n += 1
    return n


# --- Synthetic documents ------------------------------------------------------------------------------------

def contract_text(name: str = "services_agreement_a.txt") -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def make_pdf(pages: list[str]) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    for text_ in pages:
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(50, 50, 550, 800), text_, fontsize=9)
    data = doc.tobytes()
    doc.close()
    return data


def make_docx(paragraphs: list[str], page_break_after: int | None = None, table: list[list[str]] | None = None) -> bytes:
    import docx
    from docx.enum.text import WD_BREAK

    d = docx.Document()
    for i, p in enumerate(paragraphs):
        para = d.add_paragraph(p)
        if page_break_after is not None and i == page_break_after:
            para.add_run().add_break(WD_BREAK.PAGE)
    if table:
        t = d.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, val in enumerate(row):
                t.cell(r, c).text = val
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def analysed_contract(acct: Account, name: str = "services_agreement_a.txt") -> dict:
    r = acct.upload(name, contract_text(name).encode(), "text/plain")
    assert r.status_code == 201, r.text
    run_jobs()
    c = acct.get(acct.ws(f"/contracts/{r.json()['id']}")).json()
    assert c["status"] == "analyzed", c
    return c
