"""Who is asking, and are they allowed in (ported from Shots on Goal).

Cloudflare Access signs everyone in (one-time PIN) and passes a signed JWT to the app; anyone with an
email can get that far. The app keeps its own approved list. Approve/Deny buttons in the ntfy push
are single-use signed links, because the phone's ntfy app calls them without a Cloudflare login.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path

import jwt
from curl_cffi.requests import AsyncSession

log = logging.getLogger("lightsout.access")

TEAM_DOMAIN = os.environ.get("LIGHTSOUT_ACCESS_TEAM", "")  # <your-team>.cloudflareaccess.com
AUD = os.environ.get("LIGHTSOUT_ACCESS_AUD", "")
ADMIN = os.environ.get("LIGHTSOUT_ADMIN_EMAIL", "").strip().lower()
# Home-network / local mode: no Cloudflare, every request is this person. Never set it on a public site.
DEV_EMAIL = (os.environ.get("LIGHTSOUT_LOCAL_EMAIL") or os.environ.get("LIGHTSOUT_DEV_EMAIL", "")).strip().lower()
BASE_URL = os.environ.get("LIGHTSOUT_BASE_URL", "http://localhost:8787").rstrip("/")
NTFY_URL = os.environ.get("NTFY_URL", "").rstrip("/")
NTFY_TOKEN = os.environ.get("NTFY_TOKEN", "")
NTFY_TOPIC = os.environ.get("LIGHTSOUT_NTFY_TOPIC", "lightsout")
LINK_TTL = 24 * 3600

_jwks = jwt.PyJWKClient(f"https://{TEAM_DOMAIN}/cdn-cgi/access/certs", cache_keys=True, lifespan=3600)


def email_from(headers) -> str | None:
    """The signed-in email, verified against Cloudflare's signing keys. None if absent or forged."""
    if DEV_EMAIL:
        return DEV_EMAIL
    token = headers.get("cf-access-jwt-assertion")
    if not token or not AUD:
        return None
    try:
        key = _jwks.get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=AUD,
                            issuer=f"https://{TEAM_DOMAIN}")
    except Exception as exc:
        log.warning("rejected Access token: %r", exc)
        return None
    email = (claims.get("email") or "").strip().lower()
    return email or None


def is_admin(email: str | None) -> bool:
    return bool(email) and email == ADMIN


# ---- signed one-tap links -------------------------------------------------------
def _secret(data_dir: Path) -> bytes:
    path = data_dir / "link_secret"
    if not path.exists():
        data_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_urlsafe(32))
        path.chmod(0o600)
    return path.read_text().strip().encode()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign(data_dir: Path, email: str, action: str, nonce: str, now: float | None = None) -> str:
    body = _b64(json.dumps({"e": email, "a": action, "n": nonce,
                            "x": int((now or time.time()) + LINK_TTL)}).encode())
    sig = _b64(hmac.new(_secret(data_dir), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify(data_dir: Path, token: str, now: float | None = None) -> dict | None:
    """The link's {email, action, nonce} if the signature holds and it hasn't expired."""
    try:
        body, sig = token.split(".", 1)
        good = _b64(hmac.new(_secret(data_dir), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        data = json.loads(_unb64(body))
    except Exception:
        return None
    if data.get("x", 0) < (now or time.time()):
        return None
    return {"email": data["e"], "action": data["a"], "nonce": data["n"]}


# ---- ntfy -----------------------------------------------------------------------
_session: AsyncSession | None = None


async def notify(title: str, message: str, actions: list[str] | None = None, tags: str = "racing_car",
                 click: str | None = None) -> bool:
    global _session
    if not NTFY_URL:
        log.info("ntfy not configured; would have sent: %s / %s", title, message)
        return False
    if _session is None:
        _session = AsyncSession(timeout=15)
    headers = {"Title": title, "Tags": tags}
    if NTFY_TOKEN:  # a self-hosted ntfy with access control; ntfy.sh public topics need none
        headers["Authorization"] = f"Bearer {NTFY_TOKEN}"
    if actions:
        headers["Actions"] = "; ".join(actions)
    if click:
        headers["Click"] = click
    try:
        r = await _session.post(f"{NTFY_URL}/{NTFY_TOPIC}", data=message.encode(), headers=headers)
        r.raise_for_status()
        return True
    except Exception as exc:
        log.warning("ntfy push failed: %r", exc)
        return False


async def notify_request(data_dir: Path, email: str, nonce: str) -> bool:
    approve = f"{BASE_URL}/act/{sign(data_dir, email, 'approve', nonce)}"
    deny = f"{BASE_URL}/act/{sign(data_dir, email, 'deny', nonce)}"
    return await notify(
        "Lights Out: access request", f"{email} wants in.",
        actions=[f"http, Approve, {approve}, method=POST, clear=true",
                 f"http, Deny, {deny}, method=POST, clear=true"],
        click=f"{BASE_URL}/#/admin")
