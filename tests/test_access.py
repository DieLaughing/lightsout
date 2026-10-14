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


def test_group_invite(client):
    c, who, sent, app_module = client
    who["email"] = "owner@example.com"
    code = c.post("/api/admin/invites", json={"note": "Group chat", "uses": 2}).json()["code"]
    c.post("/api/admin/users", json={"email": "gone@example.com", "status": "revoked"})
    who["email"] = "gone@example.com"
    assert c.get(f"/invite/{code}", follow_redirects=False).status_code == 410  # a group link can't undo a revoke
    for n, email in enumerate(("a@example.com", "b@example.com"), 1):
        who["email"] = email
        assert c.get(f"/invite/{code}", follow_redirects=False).status_code == 307
        assert c.get("/api/me").json()["status"] == "approved"
        assert sent[-1][0] == "Lights Out: invite used" and len(app_module.store.invite(code)["uses"]) == n
    who["email"] = "c@example.com"
    assert c.get(f"/invite/{code}", follow_redirects=False).status_code == 410  # full
    who["email"] = "owner@example.com"
    inv, = [i for i in c.get("/api/admin").json()["invites"] if i["code"] == code]
    assert [u["email"] for u in inv["uses"]] == ["a@example.com", "b@example.com"] and inv["max_uses"] == 2
    c.delete(f"/api/admin/invites/{code}")
    assert app_module.store.invite(code)["max_uses"] == 2  # used invites close, they don't vanish


def test_heavy_answers_are_reused_and_gzipped(client):
    c, who, sent, app_module = client
    who["email"] = "owner@example.com"
    app_module._ready.clear()
    r1 = c.get("/api/series", headers={"Accept-Encoding": "gzip"})
    small = len(r1.content) <= 1500                                            # small answers go out as they are
    assert r1.status_code == 200 and (r1.headers.get("content-encoding") == "gzip") != small and isinstance(r1.json(), list)
    assert any(k[0] == "/api/series" for k in app_module._ready)          # kept for the next asker
    r2 = c.get("/api/series", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in r2.headers and r2.json() == r1.json()
    assert not any(k[0].startswith("/api/live") for k in app_module._ready)
    who["email"] = None
    assert c.get("/api/series").status_code == 401                        # the cache never skips the sign-in check
