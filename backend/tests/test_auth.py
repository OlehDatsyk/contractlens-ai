from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import select

from app.core.config import get_settings
from app.models import RefreshToken, User
from tests.conftest import PASSWORD, register

CSRF = {"X-Requested-With": "ContractLens"}


def test_register_login_me(client):
    acct = register(client, "carol@example.com", "Carol Example")
    me = acct.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["email"] == "carol@example.com"
    assert "password_hash" not in me.json()
    r = client.post("/api/auth/login", json={"email": "CAROL@example.com", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["access_token"]
    assert "cl_refresh" in r.cookies or "cl_refresh" in client.cookies
    ws = acct.get("/api/workspaces").json()
    assert len(ws) == 1 and ws[0]["role"] == "owner"


def test_duplicate_registration_is_generic(client):
    register(client, "dup@example.com")
    r = client.post("/api/auth/register", json={"email": "dup@example.com", "password": PASSWORD, "full_name": "X"})
    assert r.status_code == 400
    assert "already exists" not in r.json()["detail"].lower()


def test_weak_passwords_rejected(client):
    for pw in ["short1A!", "alllowercaseletters", "password123", "Password1"]:
        r = client.post("/api/auth/register", json={"email": "weak@example.com", "password": pw, "full_name": "W"})
        assert r.status_code == 422, pw


def test_password_stored_as_argon2_hash(client, db):
    register(client, "hash@example.com")
    user = db.scalar(select(User).where(User.email == "hash@example.com"))
    assert user.password_hash.startswith("$argon2id$") and PASSWORD not in user.password_hash


def test_wrong_password_and_lockout(client, db):
    register(client, "lock@example.com")
    for _ in range(get_settings().max_failed_logins):
        r = client.post("/api/auth/login", json={"email": "lock@example.com", "password": "Wrong-Password-1"})
        assert r.status_code == 401
        assert r.json()["detail"] == "Invalid email or password"
    r = client.post("/api/auth/login", json={"email": "lock@example.com", "password": PASSWORD})
    assert r.status_code == 429  # temporarily locked even with the right password
    r = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
    assert r.status_code == 401 and r.json()["detail"] == "Invalid email or password"


def test_unauthenticated_and_tampered_tokens(client):
    acct = register(client)
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401
    payload = jwt.decode(acct.token, options={"verify_signature": False})
    forged = jwt.encode(payload, "wrong-secret-wrong-secret-wrong-secret", algorithm="HS256")
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    none_alg = jwt.encode(payload, None, algorithm="none") if hasattr(jwt, "encode") else ""
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {none_alg}"}).status_code == 401
    expired = dict(payload, exp=int(time.time()) - 10)
    tok = jwt.encode(expired, get_settings().jwt_secret_key, algorithm="HS256")
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401
    wrong_type = dict(payload, type="refresh")
    tok = jwt.encode(wrong_type, get_settings().jwt_secret_key, algorithm="HS256")
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_refresh_rotation_and_reuse_detection(client, db):
    register(client, "rot@example.com")
    first = client.cookies.get("cl_refresh")
    assert client.post("/api/auth/refresh").status_code == 403  # CSRF header required
    r = client.post("/api/auth/refresh", headers=CSRF)
    assert r.status_code == 200
    second = client.cookies.get("cl_refresh")
    assert second and second != first
    # Replaying the rotated token revokes the whole family.
    client.cookies.set("cl_refresh", first, path="/api/auth")
    assert client.post("/api/auth/refresh", headers=CSRF).status_code == 401
    client.cookies.set("cl_refresh", second, path="/api/auth")
    assert client.post("/api/auth/refresh", headers=CSRF).status_code == 401
    assert all(t.revoked_at for t in db.scalars(select(RefreshToken)))


def test_logout_revokes_refresh(client):
    register(client)
    assert client.post("/api/auth/logout", headers=CSRF).status_code == 204
    assert client.post("/api/auth/refresh", headers=CSRF).status_code == 401


def test_change_password_invalidates_tokens(client):
    acct = register(client, "chg@example.com")
    new_pw = "An0ther-Strong-Pass!"
    r = acct.post("/api/auth/change-password", json={"current_password": "nope", "new_password": new_pw})
    assert r.status_code == 400
    r = acct.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": new_pw})
    assert r.status_code == 204
    assert acct.get("/api/auth/me").status_code == 401  # old access token invalid (token_version bumped)
    assert client.post("/api/auth/login", json={"email": "chg@example.com", "password": new_pw}).status_code == 200


def test_password_reset_flow(client, db, monkeypatch):
    register(client, "reset@example.com")
    sent: dict[str, str] = {}

    def fake_send(to, subject, text):
        sent["to"], sent["text"] = to, text
        return True

    monkeypatch.setattr("app.api.routes.auth.send_email", fake_send)
    r = client.post("/api/auth/forgot-password", json={"email": "reset@example.com"})
    assert r.status_code == 202
    r2 = client.post("/api/auth/forgot-password", json={"email": "missing@example.com"})
    assert r2.status_code == 202 and r2.json() == r.json()  # no account enumeration
    token = sent["text"].split("token=")[1].split()[0]
    new_pw = "Brand-New-Pass-42"
    assert client.post("/api/auth/reset-password", json={"token": "x" * 40, "new_password": new_pw}).status_code == 400
    assert client.post("/api/auth/reset-password", json={"token": token, "new_password": new_pw}).status_code == 204
    assert client.post("/api/auth/reset-password", json={"token": token, "new_password": new_pw}).status_code == 400  # single use
    assert client.post("/api/auth/login", json={"email": "reset@example.com", "password": new_pw}).status_code == 200


def test_expired_reset_token(client, db, monkeypatch):
    register(client, "exp@example.com")
    captured = {}
    monkeypatch.setattr("app.api.routes.auth.send_email", lambda to, s, t: captured.setdefault("t", t) and True)
    client.post("/api/auth/forgot-password", json={"email": "exp@example.com"})
    from app.models import PasswordResetToken

    for row in db.scalars(select(PasswordResetToken)):
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    token = captured["t"].split("token=")[1].split()[0]
    assert client.post("/api/auth/reset-password", json={"token": token, "new_password": "Brand-New-Pass-42"}).status_code == 400


def test_profile_update(client):
    acct = register(client)
    r = acct.patch("/api/auth/me", json={"full_name": "  New   Name ", "email_notifications": False})
    assert r.status_code == 200 and r.json()["full_name"] == "New Name" and r.json()["email_notifications"] is False


def test_logout_all(client):
    acct = register(client)
    assert acct.post("/api/auth/logout-all").status_code == 204
    assert acct.get("/api/auth/me").status_code == 401
