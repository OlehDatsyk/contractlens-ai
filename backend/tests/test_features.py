"""Comparison, reports, deadlines/reminders, dashboard, worker reliability and API hardening."""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import Contract, Deadline, DeadlineReminder, Job, JobStatus, Notification
from tests.conftest import analysed_contract, contract_text, run_jobs

# --- Comparison ---------------------------------------------------------------------------------------------


def test_compare_contracts(client, alice):
    a = analysed_contract(alice, "services_agreement_a.txt")
    b = analysed_contract(alice, "services_agreement_b.txt")
    r = alice.get(alice.ws(f"/compare?a={a['id']}&b={b['id']}"))
    assert r.status_code == 200, r.text
    cmp = r.json()
    topics = {t["key"]: t for t in cmp["topics"]}
    assert topics["governing_law"]["status"] == "identical"
    assert topics["payment_terms"]["status"] == "different"
    assert topics["liability"]["status"] == "different"
    assert any(c["heading"] == "Insurance" for c in cmp["clauses"]["added"])
    assert any(c["heading"] == "Non-Compete" for c in cmp["clauses"]["removed"])
    services = [m for m in cmp["clauses"]["identical"] if (m["a"]["section_ref"] or "").startswith("1.")]
    assert services, "unchanged service clauses should be reported as identical"
    modified = cmp["clauses"]["modified"]
    assert modified and all(0 <= m["similarity"] < 1 for m in modified)
    assert any(seg["op"] in ("insert", "delete") for seg in modified[0]["diff"])
    resolved = {x["title"] for x in cmp["risks"]["resolved_in_b"]}
    assert "Unlimited liability" in resolved
    assert cmp["a"]["risk_score"] > cmp["b"]["risk_score"]


def test_compare_requires_two_analysed_contracts(client, alice):
    a = analysed_contract(alice)
    pending = alice.upload("p.txt", contract_text().encode(), "text/plain", analyze=False).json()
    assert alice.get(alice.ws(f"/compare?a={a['id']}&b={a['id']}")).status_code == 400
    assert alice.get(alice.ws(f"/compare?a={a['id']}&b={pending['id']}")).status_code == 409


# --- Reports ------------------------------------------------------------------------------------------------


def test_reports_pdf_csv_json(client, alice):
    c = analysed_contract(alice)
    for fmt, magic in (("pdf", b"%PDF"), ("csv", "﻿".encode()), ("json", b"{")):
        rep = alice.post(alice.ws("/reports"), json={"contract_id": c["id"], "format": fmt})
        assert rep.status_code == 201, rep.text
        link = alice.post(alice.ws(f"/reports/{rep.json()['id']}/download-link")).json()["url"]
        body = client.get(link)
        assert body.status_code == 200 and body.content.startswith(magic)
        if fmt == "json":
            data = json.loads(body.content)
            assert data["overview"]["title"] == c["title"] and "not provide legal advice" in data["disclaimer"]
            assert any(r["page"] is None and r["section"] for r in data["risks"])
        if fmt == "csv":
            rows = list(csv.reader(io.StringIO(body.content.decode("utf-8-sig"))))
            assert rows[0][0] == "section" and any(r[0] == "risk_finding" for r in rows)
    assert len(alice.get(alice.ws("/reports")).json()) == 3


def test_csv_formula_injection_escaped(client, alice):
    text = contract_text().replace("MASTER SERVICES AGREEMENT", "=HYPERLINK(\"http://evil\") SERVICES AGREEMENT")
    r = alice.upload("inj.txt", text.encode(), "text/plain").json()
    run_jobs()
    alice.patch(alice.ws(f"/contracts/{r['id']}"), json={"title": "=cmd|' /C calc'!A0"})
    rep = alice.post(alice.ws("/reports"), json={"contract_id": r["id"], "format": "csv"}).json()
    body = client.get(alice.post(alice.ws(f"/reports/{rep['id']}/download-link")).json()["url"]).content.decode("utf-8-sig")
    for row in csv.reader(io.StringIO(body)):
        for cell in row:
            assert not cell.startswith(("=", "+", "@")), cell


def test_report_requires_analysis_and_role(client, alice):
    pending = alice.upload("p.txt", contract_text().encode(), "text/plain", analyze=False).json()
    assert alice.post(alice.ws("/reports"), json={"contract_id": pending["id"], "format": "pdf"}).status_code == 409


# --- Deadlines ----------------------------------------------------------------------------------------------


def test_auto_deadlines_from_analysis(client, alice, db):
    c = analysed_contract(alice)
    deadlines = alice.get(alice.ws(f"/deadlines?contract_id={c['id']}")).json()
    types = {d["deadline_type"]: d for d in deadlines}
    if date.today() <= date(2027, 3, 1):
        assert types["expiration"]["due_date"] == "2027-03-01"
        assert types["notice"]["due_date"] == (date(2027, 3, 1) - timedelta(days=90)).isoformat()
        assert all(d["source"] == "auto" for d in deadlines)


def test_deadline_crud_and_validation(client, alice):
    r = alice.post(alice.ws("/deadlines"), json={"title": "Send renewal notice", "due_date": "2030-05-01",
                                                  "deadline_type": "notice", "reminder_days": [14, 2, 14]})
    assert r.status_code == 201 and r.json()["reminder_days"] == [14, 2]
    did = r.json()["id"]
    assert alice.post(alice.ws("/deadlines"), json={"title": "x", "due_date": "2030-05-01", "reminder_days": [400]}).status_code == 422
    assert alice.patch(alice.ws(f"/deadlines/{did}"), json={"status": "done"}).json()["status"] == "done"
    assert alice.patch(alice.ws(f"/deadlines/{did}"), json={"status": "bogus"}).status_code == 422
    assert len(alice.get(alice.ws("/deadlines?start=2030-01-01&end=2030-12-31")).json()) == 1
    assert alice.delete(alice.ws(f"/deadlines/{did}")).status_code == 204


def test_reminders_fire_once_without_email(client, alice, db, monkeypatch):
    from app.worker.runner import send_reminders

    sent_emails = []
    monkeypatch.setattr("app.services.notifications.smtplib.SMTP", lambda *a, **k: sent_emails.append(a))
    due = (date.today() + timedelta(days=5)).isoformat()
    alice.post(alice.ws("/deadlines"), json={"title": "Pay invoice", "due_date": due, "reminder_days": [30, 7, 1]})
    assert send_reminders(db) == 1
    assert send_reminders(db) == 0  # each offset fires at most once
    notes = list(db.scalars(select(Notification).where(Notification.kind == "deadline_reminder")))
    assert len(notes) == 1 and not notes[0].emailed
    assert {r.days_before for r in db.scalars(select(DeadlineReminder))} == {30, 7}
    assert sent_emails == []  # no email provider configured -> nothing sent


def test_reminder_email_when_configured(client, alice, db, monkeypatch):
    from app.worker.runner import send_reminders

    s = get_settings()
    monkeypatch.setattr(s, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(s, "smtp_from", "noreply@example.test")
    delivered = []

    class FakeSMTP:
        def __init__(self, *a, **k): ...
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, **k): ...
        def login(self, *a): ...
        def send_message(self, msg): delivered.append(msg["To"])

    monkeypatch.setattr("app.services.notifications.smtplib.SMTP", FakeSMTP)
    alice.post(alice.ws("/deadlines"), json={"title": "Notice", "due_date": date.today().isoformat(), "reminder_days": [0]})
    send_reminders(db)
    assert delivered == [alice.email]


# --- Dashboard / notifications ------------------------------------------------------------------------------


def test_dashboard_empty_and_populated(client, alice):
    empty = alice.get(alice.ws("/dashboard")).json()
    assert empty["totals"]["contracts"] == 0 and empty["recent"] == []
    assert all(w["uploaded"] == 0 for w in empty["activity"])
    analysed_contract(alice)
    analysed_contract(alice, "mutual_nda.txt")
    d = alice.get(alice.ws("/dashboard")).json()
    assert d["totals"]["contracts"] == 2 and d["totals"]["analyzed"] == 2 and d["totals"]["high_risk"] == 1
    assert sum(w["uploaded"] for w in d["activity"]) == 2
    assert len(d["recent"]) == 2


def test_notifications(client, alice):
    analysed_contract(alice)
    notes = alice.get("/api/notifications").json()
    assert notes and notes[0]["kind"] == "analysis_complete"
    assert alice.post("/api/notifications/read").status_code == 204
    assert alice.get("/api/notifications?unread_only=true").json() == []


# --- Worker reliability -------------------------------------------------------------------------------------


def test_duplicate_analysis_jobs_prevented(client, alice, db):
    c = alice.upload("dup.txt", contract_text().encode(), "text/plain").json()
    for _ in range(3):
        assert alice.post(alice.ws(f"/contracts/{c['id']}/analyze")).status_code == 202
    assert db.scalar(select(func.count()).select_from(Job).where(Job.status == JobStatus.queued)) == 1
    run_jobs()
    assert alice.post(alice.ws(f"/contracts/{c['id']}/analyze")).json()["status"] == "queued"
    run_jobs()
    assert alice.get(alice.ws(f"/contracts/{c['id']}")).json()["status"] == "analyzed"
    # Re-analysis replaces results instead of duplicating them
    assert len(alice.get(alice.ws(f"/contracts/{c['id']}/findings")).json()) == len({f["title"] for f in alice.get(alice.ws(f"/contracts/{c['id']}/findings")).json()})


def test_transient_failure_is_retried(client, alice, db, monkeypatch):
    calls = {"n": 0}
    from app.services import analysis

    real = analysis.analyze_contract

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("database blip")
        return real(*a, **k)

    monkeypatch.setattr("app.worker.runner.analyze_contract", flaky)
    c = alice.upload("flaky.txt", contract_text().encode(), "text/plain").json()
    run_jobs()
    assert alice.get(alice.ws(f"/contracts/{c['id']}")).json()["status"] == "queued"
    job = db.scalar(select(Job))
    job.run_after = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    run_jobs()
    assert alice.get(alice.ws(f"/contracts/{c['id']}")).json()["status"] == "analyzed"


def test_stale_jobs_recovered(client, alice, db):
    from app.worker.queue import recover_stale

    c = alice.upload("stale.txt", contract_text().encode(), "text/plain").json()
    job = db.scalar(select(Job))
    job.status, job.locked_at, job.attempts = JobStatus.running, datetime.now(UTC) - timedelta(hours=2), 1
    db.get(Contract, job.contract_id).status = "processing"
    db.commit()
    assert recover_stale(db) == 1
    db.refresh(job)
    assert job.status == JobStatus.queued
    run_jobs()
    assert alice.get(alice.ws(f"/contracts/{c['id']}")).json()["status"] == "analyzed"


def test_retention_policy_deletes_old_contracts(client, alice, db):
    from app.worker.runner import apply_retention

    c = alice.upload("old.txt", contract_text().encode(), "text/plain").json()
    run_jobs()
    alice.patch(alice.ws("/settings"), json={"retention_days": 30})
    row = db.get(Contract, __import__("uuid").UUID(c["id"]))
    row.created_at = datetime.now(UTC) - timedelta(days=31)
    db.commit()
    assert apply_retention(db) == 1
    assert alice.get(alice.ws(f"/contracts/{c['id']}")).status_code == 404
    assert db.scalar(select(func.count()).select_from(Deadline)) == 0


# --- API hardening ------------------------------------------------------------------------------------------


def test_health_and_security_headers(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    r = client.get("/health/ready")
    assert r.status_code == 200 and r.json()["checks"]["database"] == "ok"
    h = client.get("/api/health").headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in h["content-security-policy"]


def test_request_size_limit(client, alice):
    r = client.post("/api/auth/login", content=b"x" * (2 * 1024 * 1024), headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_validation_errors_do_not_echo_input(client):
    r = client.post("/api/auth/login", json={"email": "not-an-email", "password": "MySecretPassword!"})
    assert r.status_code == 422 and "MySecretPassword" not in r.text


def test_rate_limiting(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    codes = [client.post("/api/auth/login", json={"email": "rl@example.com", "password": "Wrong-Pass-123"}).status_code for _ in range(12)]
    assert 429 in codes


def test_cors_is_restricted(client):
    r = client.options("/api/auth/login", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert r.headers.get("access-control-allow-origin") != "https://evil.example"


def test_production_config_rejects_insecure_defaults(monkeypatch):
    import pytest

    from app.core.config import Settings

    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        Settings(environment="production", jwt_secret_key="short", _env_file=None)
    with pytest.raises(ValueError, match="ephemeral"):
        Settings(environment="production", jwt_secret_key="x" * 40, encryption_key="k", _env_file=None,
                 database_url="postgresql://u:p@h/db")
    ok = Settings(environment="production", jwt_secret_key="x" * 40, encryption_key="k", storage_backend="database",
                  database_url="postgres://u:p@h/db", _env_file=None)
    assert ok.database_url.startswith("postgresql+psycopg://")
