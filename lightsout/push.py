"""Web push: notifications straight to a phone or browser, no extra app. On iPhone the site must be added to
the home screen first (iOS 16.4+)."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

log = logging.getLogger("lightsout.push")


class Pusher:
    def __init__(self, data_dir: Path, contact: str):
        self.path = data_dir / "vapid_private.pem"
        if not self.path.exists():
            data_dir.mkdir(parents=True, exist_ok=True)
            v = Vapid()
            v.generate_keys()
            v.save_key(str(self.path))
            self.path.chmod(0o600)
        self.vapid = Vapid.from_file(str(self.path))
        self.contact = f"mailto:{contact or 'admin@example.com'}"

    @property
    def public_key(self) -> str:
        """The applicationServerKey browsers need: the raw public point, base64url."""
        raw = self.vapid.public_key.public_bytes(serialization.Encoding.X962,
                                                 serialization.PublicFormat.UncompressedPoint)
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    def _send(self, sub: dict, payload: dict) -> str:
        try:
            webpush(subscription_info=sub, data=json.dumps(payload), vapid_private_key=self.vapid,
                    vapid_claims={"sub": self.contact}, ttl=3600, timeout=10)
            return "ok"
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):
                return "gone"  # the browser unsubscribed or the app was removed
            log.warning("push failed (%s): %s", status, exc)
            return "error"
        except Exception as exc:
            log.warning("push failed: %r", exc)
            return "error"

    async def send(self, sub: dict, payload: dict) -> str:
        return await asyncio.to_thread(self._send, sub, payload)
