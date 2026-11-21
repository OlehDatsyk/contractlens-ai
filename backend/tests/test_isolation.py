"""Cross-workspace isolation tests using direct API requests (not the UI)."""

from __future__ import annotations

from tests.conftest import analysed_contract


def test_cross_workspace_access_is_blocked(client, alice, bob):
    c = analysed_contract(alice)
    cid, wa, wb = c["id"], alice.workspace_id, bob.workspace_id
    conv = alice.post(alice.ws(f"/contracts/{cid}/conversations"), json={}).json()
    rep = alice.post(alice.ws("/reports"), json={"contract_id": cid, "format": "json"}).json()
    dl = alice.post(alice.ws("/deadlines"), json={"title": "x", "due_date": "2031-01-01", "contract_id": cid}).json()

    # Bob uses Alice's workspace id directly -> not a member -> 404 everywhere.
    for path in ["", "/contracts", f"/contracts/{cid}", f"/contracts/{cid}/analysis", f"/contracts/{cid}/findings",
                 f"/contracts/{cid}/clauses", f"/contracts/{cid}/obligations", f"/contracts/{cid}/conversations",
                 "/reports", "/deadlines", "/dashboard", "/members", "/settings", f"/compare?a={cid}&b={cid}"]:
        assert bob.get(f"/api/workspaces/{wa}{path}").status_code == 404, path
    assert bob.post(f"/api/workspaces/{wa}/contracts/{cid}/download-link").status_code == 404
    assert bob.post(f"/api/workspaces/{wa}/reports/{rep['id']}/download-link").status_code == 404
    assert bob.delete(f"/api/workspaces/{wa}/contracts/{cid}").status_code == 404
    assert bob.patch(f"/api/workspaces/{wa}/deadlines/{dl['id']}", json={"title": "pwned"}).status_code == 404

    # Bob uses HIS workspace id with Alice's resource ids -> resources are scoped by workspace -> 404.
    for path in [f"/contracts/{cid}", f"/contracts/{cid}/analysis", f"/contracts/{cid}/findings",
                 f"/contracts/{cid}/conversations/{conv['id']}/messages"]:
        assert bob.get(f"/api/workspaces/{wb}{path}").status_code == 404, path
    assert bob.post(f"/api/workspaces/{wb}/contracts/{cid}/download-link").status_code == 404
    assert bob.post(f"/api/workspaces/{wb}/reports/{rep['id']}/download-link").status_code == 404
    assert bob.post(f"/api/workspaces/{wb}/reports", json={"contract_id": cid, "format": "pdf"}).status_code == 404
    assert bob.delete(f"/api/workspaces/{wb}/deadlines/{dl['id']}").status_code == 404
    assert bob.post(f"/api/workspaces/{wb}/deadlines", json={"title": "x", "due_date": "2031-01-01", "contract_id": cid}).status_code == 404
    assert bob.get(f"/api/workspaces/{wb}/compare?a={cid}&b={cid}").status_code in (400, 404)
    assert bob.post(f"/api/workspaces/{wb}/contracts/{cid}/conversations/{conv['id']}/messages",
                    json={"content": "When does it expire?"}).status_code == 404

    # Bob's own lists contain nothing of Alice's.
    assert bob.get(f"/api/workspaces/{wb}/contracts").json()["total"] == 0
    assert bob.get(f"/api/workspaces/{wb}/reports").json() == []
    assert bob.get(f"/api/workspaces/{wb}/contracts?q=Northwind&search_content=true").json()["total"] == 0


def test_conversations_are_private_per_user(client, alice):
    from tests.conftest import register

    c = analysed_contract(alice)
    colleague = register(client, "colleague@example.com")
    token = alice.post(alice.ws("/invitations"), json={"email": colleague.email, "role": "viewer"}).json()["invite_link"].split("token=")[1]
    colleague.post("/api/invitations/accept", json={"token": token})
    conv = alice.post(alice.ws(f"/contracts/{c['id']}/conversations"), json={}).json()
    assert colleague.get(alice.ws(f"/contracts/{c['id']}/conversations/{conv['id']}/messages")).status_code == 404
    assert colleague.get(alice.ws(f"/contracts/{c['id']}/conversations")).json() == []


def test_signed_download_links(client, alice, bob):
    c = analysed_contract(alice)
    link = alice.post(alice.ws(f"/contracts/{c['id']}/download-link")).json()["url"]
    r = client.get(link)
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.headers["cache-control"].startswith("no-store")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert b"MASTER SERVICES AGREEMENT" in r.content
    # Tampered tokens are rejected
    body, sig = link.rsplit("/", 1)[1].split(".")
    assert client.get(f"/api/downloads/{body}.{sig[:-2]}AA").status_code == 404
    assert client.get("/api/downloads/not-a-token").status_code == 404


def test_download_link_revoked_when_membership_removed(client, alice):
    from tests.conftest import register

    c = analysed_contract(alice)
    member = register(client, "member@example.com")
    token = alice.post(alice.ws("/invitations"), json={"email": member.email, "role": "viewer"}).json()["invite_link"].split("token=")[1]
    member.post("/api/invitations/accept", json={"token": token})
    link = member.post(alice.ws(f"/contracts/{c['id']}/download-link")).json()["url"]
    uid = next(m["user_id"] for m in alice.get(alice.ws("/members")).json() if m["email"] == member.email)
    assert alice.delete(alice.ws(f"/members/{uid}")).status_code == 204
    assert client.get(link).status_code == 404


def test_expired_download_link(client, alice, monkeypatch):
    c = analysed_contract(alice)
    link = alice.post(alice.ws(f"/contracts/{c['id']}/download-link")).json()["url"]
    import time as _time

    real = _time.time
    monkeypatch.setattr("app.core.security.time.time", lambda: real() + 10_000)
    assert client.get(link).status_code == 404
