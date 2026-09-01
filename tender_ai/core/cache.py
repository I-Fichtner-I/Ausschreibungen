"""Einfacher Dateicache fuer HTTP-Antworten.

Zweck: wiederholte Laeufe waehrend der Entwicklung und beim Debuggen belasten
die Portale nicht erneut. Der Cache ist bewusst simpel (JSON-Datei je
Request-Fingerprint) und jederzeit loeschbar.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Any


class ResponseCache:
    def __init__(self, directory: Path, ttl_seconds: int = 900, enabled: bool = True) -> None:
        self.directory = Path(directory)
        self.ttl_seconds = ttl_seconds
        self.enabled = enabled
        if self.enabled:
            self.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def make_key(method: str, url: str, body: bytes | None = None) -> str:
        digest = hashlib.sha256()
        digest.update(method.upper().encode())
        digest.update(b"\x00")
        digest.update(url.encode())
        digest.update(b"\x00")
        if body:
            digest.update(body)
        return digest.hexdigest()

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if time.time() - payload.get("stored_at", 0) > self.ttl_seconds:
            return None
        payload["content"] = base64.b64decode(payload["content_b64"])
        return payload

    def set(
        self,
        key: str,
        *,
        status_code: int,
        content: bytes,
        headers: dict[str, str] | None = None,
    ) -> None:
        if not self.enabled:
            return
        payload = {
            "stored_at": time.time(),
            "status_code": status_code,
            "headers": headers or {},
            "content_b64": base64.b64encode(content).decode("ascii"),
        }
        tmp = self._path(key).with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(self._path(key))

    def clear(self) -> int:
        if not self.directory.is_dir():
            return 0
        removed = 0
        for file in self.directory.glob("*.json"):
            file.unlink(missing_ok=True)
            removed += 1
        return removed
