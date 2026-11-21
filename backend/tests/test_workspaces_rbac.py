from __future__ import annotations

from tests.conftest import contract_text, register, run_jobs


def _invite(owner, email, role):
    r = owner.post(owner.ws("/invitations"), json={"email": email, "role": role})
    assert r.status_code == 201, r.text
    return r.json()["invite_link"].split("token=")[1]


def _join(owner, member, role):
    token = _invite(owner, member.email, role)
    r = member.post("/api/invitations/accept", json={"token": token})
    assert r.status_code == 200, r.text


def test_create_workspace_and_list(client, alice):
    r = alice.post("/api/workspaces", json={"name": "  Legal   Ops  "})
    assert r.status_code == 201 and r.json()["name"] == "Legal Ops" and r.json()["role"] == "owner"
    assert len(alice.get("/api/workspaces").json()) == 2


def test_invitation_requires_matching_email_and_token(client, alice, bob):
    token = _invite(alice, "someone-else@example.com", "viewer")
    r = bob.post("/api/invitations/accept", json={"token": token})
    assert r.status_code == 403
    assert bob.post("/api/invitations/accept", json={"token": "z" * 43}).status_code == 400
    token = _invite(alice, bob.email, "analyst")
    assert bob.post("/api/invitations/accept", json={"token": token}).status_code == 200
    assert bob.post("/api/invitations/accept", json={"token": token}).status_code == 400  # single use
    roles = {w["id"]: w["role"] for w in bob.get("/api/workspaces").json()}
    assert roles[alice.workspace_id] == "analyst"


def test_viewer_permissions(client, alice):
    viewer = register(client, "viewer@example.com")
    _join(alice, viewer, "viewer")
    ws = alice.workspace_id
    up = viewer.upload("a.txt", contract_text().encode(), "text/plain", workspace_id=ws)
    assert up.status_code == 403
    assert viewer.get(f"/api/workspaces/{ws}/contracts").status_code == 200
    assert viewer.post(f"/api/workspaces/{ws}/folders", json={"name": "X"}).status_code == 403
    assert viewer.post(f"/api/workspaces/{ws}/deadlines", json={"title": "t", "due_date": "2030-01-01"}).status_code == 403
    assert viewer.get(f"/api/workspaces/{ws}/audit").status_code == 403
    assert viewer.patch(f"/api/workspaces/{ws}/settings", json={"name": "hacked"}).status_code == 403


def test_analyst_permissions(client, alice):
    analyst = register(client, "analyst@example.com")
    _join(alice, analyst, "analyst")
    ws = alice.workspace_id
    own = analyst.upload("mine.txt", contract_text().encode(), "text/plain", workspace_id=ws)
    assert own.status_code == 201
    other = alice.upload("alices.txt", contract_text().encode(), "text/plain")
    run_jobs()
    assert analyst.delete(f"/api/workspaces/{ws}/contracts/{other.json()['id']}").status_code == 403
    assert analyst.delete(f"/api/workspaces/{ws}/contracts/{own.json()['id']}").status_code == 204
    assert analyst.post(f"/api/workspaces/{ws}/invitations", json={"email": "x@example.com", "role": "viewer"}).status_code == 403
    assert analyst.get(f"/api/workspaces/{ws}/audit").status_code == 403


def test_admin_cannot_escalate(client, alice):
    admin = register(client, "admin@example.com")
    _join(alice, admin, "admin")
    target = register(client, "target@example.com")
    _join(alice, target, "viewer")
    ws = alice.workspace_id
    assert admin.patch(f"/api/workspaces/{ws}/members/{_uid(alice, target.email)}", json={"role": "analyst"}).status_code == 200
    assert admin.patch(f"/api/workspaces/{ws}/members/{_uid(alice, target.email)}", json={"role": "owner"}).status_code == 403
    assert admin.patch(f"/api/workspaces/{ws}/members/{_uid(alice, alice.email)}", json={"role": "viewer"}).status_code == 403
    assert admin.post(f"/api/workspaces/{ws}/invitations", json={"email": "o@example.com", "role": "owner"}).status_code == 403
    # Admins can read settings but not change AI configuration (owner only)
    assert admin.patch(f"/api/workspaces/{ws}/settings", json={"ai_provider": "openai", "ai_api_key": "sk-test"}).status_code == 403
    assert admin.patch(f"/api/workspaces/{ws}/settings", json={"retention_days": 365}).status_code == 200


def _uid(acct, email):
    return next(m["user_id"] for m in acct.get(acct.ws("/members")).json() if m["email"] == email)


def test_last_owner_protection(client, alice):
    me = _uid(alice, alice.email)
    assert alice.patch(alice.ws(f"/members/{me}"), json={"role": "admin"}).status_code == 400
    assert alice.delete(alice.ws(f"/members/{me}")).status_code == 400


def test_member_can_leave(client, alice, bob):
    _join(alice, bob, "viewer")
    assert bob.delete(f"/api/workspaces/{alice.workspace_id}/members/{_uid(alice, bob.email)}").status_code == 204
    assert bob.get(f"/api/workspaces/{alice.workspace_id}/contracts").status_code == 404


def test_folders_crud(client, alice):
    r = alice.post(alice.ws("/folders"), json={"name": "Vendors"})
    assert r.status_code == 201
    assert alice.post(alice.ws("/folders"), json={"name": "Vendors"}).status_code == 409
    fid = r.json()["id"]
    c = alice.upload("v.txt", contract_text().encode(), "text/plain", analyze=False).json()
    assert alice.patch(alice.ws(f"/contracts/{c['id']}"), json={"folder_id": fid}).json()["folder_id"] == fid
    assert alice.get(alice.ws("/folders")).json()[0]["contract_count"] == 1
    assert alice.get(alice.ws(f"/contracts?folder_id={fid}")).json()["total"] == 1
    assert alice.delete(alice.ws(f"/folders/{fid}")).status_code == 204
    assert alice.get(alice.ws(f"/contracts/{c['id']}")).json()["folder_id"] is None


def test_workspace_ai_key_encrypted_and_masked(client, alice, db):
    r = alice.patch(alice.ws("/settings"), json={"ai_provider": "openai", "ai_api_key": "sk-test-1234567890abcd"})
    assert r.status_code == 200
    body = r.json()["ai"]
    assert body["workspace_key_set"] and body["workspace_key_hint"].endswith("abcd")
    assert "sk-test-1234567890abcd" not in r.text
    from app.models import Workspace

    ws = db.get(Workspace, __import__("uuid").UUID(alice.workspace_id))
    assert ws.ai_api_key_encrypted and "sk-test" not in ws.ai_api_key_encrypted


def test_delete_workspace_requires_confirmation(client, alice):
    ws2 = alice.post("/api/workspaces", json={"name": "Temp"}).json()["id"]
    alice.upload("t.txt", contract_text().encode(), "text/plain", workspace_id=ws2)
    assert alice.delete(f"/api/workspaces/{ws2}", json={"confirm_name": "wrong"}).status_code == 400
    assert alice.delete(f"/api/workspaces/{ws2}", json={"confirm_name": "Temp"}).status_code == 204
    assert alice.get(f"/api/workspaces/{ws2}").status_code == 404


def test_audit_log_records_actions(client, alice):
    alice.upload("t.txt", contract_text().encode(), "text/plain")
    actions = [e["action"] for e in alice.get(alice.ws("/audit")).json()]
    assert "contract.upload" in actions
    assert all("text" not in e["details"] for e in alice.get(alice.ws("/audit")).json())
