"""Raw payload store: every vendor response and file, unchanged, one folder per user.

Objects live at users/<user_id>/<path> in the raw bucket; each write is also listed as a
`raw_payloads` row so later modules can re-process everything from the original data.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone


class RawStore:
    """Writes to a GCS bucket; with bucket=None it only records what would be stored (tests, dry runs)."""

    def __init__(self, bucket, user_id: str, source: str, run_id: str | None = None) -> None:
        self.bucket, self.user_id, self.source, self.run_id = bucket, user_id, source, run_id
        self.rows: list[dict] = []

    def path(self, path: str) -> str:
        return f"users/{self.user_id}/{path}"

    def put_bytes(self, path: str, content: bytes, content_type: str, endpoint: str, day: str | None = None) -> str | None:
        if self.bucket is None:
            return None
        full = self.path(path)
        self.bucket.blob(full).upload_from_string(content, content_type=content_type)
        ref = f"gs://{self.bucket.name}/{full}"
        self.rows.append({
            "user_id": self.user_id, "source": self.source, "endpoint": endpoint,
            "window_start": day, "window_end": day, "object_path": ref,
            "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content),
            "fetched_at": datetime.now(timezone.utc).isoformat(), "ingest_run_id": self.run_id})
        return ref

    def put_json(self, path: str, obj, endpoint: str, day: str | None = None) -> str | None:
        body = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str).encode()
        return self.put_bytes(path, body, "application/json", endpoint, day)

    def drain(self) -> list[dict]:
        rows, self.rows = self.rows, []
        return rows
