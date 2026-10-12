"""Access gate: approval list, single-use ntfy links, invites. Cloudflare's JWT check is stubbed."""
import importlib
import os

import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LIGHTSOUT_DATA", str(tmp_path))
    monkeypatch.setenv("LIGHTSOUT_NO_REFRESH", "1")
    monkeypatch.setenv("LIGHTSOUT_ADMIN_EMAIL", "owner@example.com")
    from lightsout import access, app as app_module
    importlib.reload(access)
    app_module = importlib.reload(app_module)
    who = {"email": None}
    monkeypatch.setattr(access, "email_from", lambda headers: who["email"])
    sent = []

    async def fake_notify(title, message, actions=None, tags="", click=None):
        sent.append((title, actions))
        return True
    monkeypatch.setattr(access, "notify", fake_notify)
    from fastapi.testclient import TestClient
    with TestClient(app_module.app) as c:
        yield c, who, sent, app_module


def test_request_approve_flow(client):
    c, who, sent, app_module = client
    who["email"] = None
    assert c.get("/api/series").status_code == 401
    who["email"] = "friend@example.com"
    assert c.get("/api/me").json()["status"] == "none"
    assert c.get("/api/series").status_code == 403
    assert c.post("/api/request").json() == {"status": "pending"}
    (title, actions), = sent
    approve = actions[0].split(", ")[2]  # "http, Approve, <url>, method=POST, clear=true"
    token = approve.rsplit("/act/", 1)[1]
    assert c.post(f"/act/{token}").status_code == 200
    assert c.get("/api/series").status_code == 200
    assert c.post(f"/act/{token}").text.endswith("already approved.")  # second tap is harmless
    tampered = token[:-2] + ("aa" if not token.endswith("aa") else "bb")
    assert c.post(f"/act/{tampered}").status_code == 410


def test_owner_and_invites(client):
    c, who, sent, app_module = client
    who["email"] = "owner@example.com"
    assert c.get("/api/me").json()["admin"] is True
    link = c.post("/api/admin/invites", json={"note": "Sam"}).json()["link"]
    code = link.rsplit("/invite/", 1)[1]
    who["email"] = "sam@example.com"
    assert c.get("/api/admin").status_code == 403
    assert c.get(f"/invite/{code}", follow_redirects=False).status_code == 307
    assert c.get("/api/me").json()["status"] == "approved"
    who["email"] = "other@example.com"
    assert c.get(f"/invite/{code}", follow_redirects=False).status_code == 410  # single use
