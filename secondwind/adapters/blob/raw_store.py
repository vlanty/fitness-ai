"""Raw payload store: every vendor response and file, unchanged, one folder per user.

Objects live at users/<user_id>/<path> in the raw bucket; each write is also listed as a
`raw_payloads` row so later modules can re-process everything from the original data.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone


class RawStore:
    """Writes to a GCS bucket. bucket=None stores nothing and returns no refs (tests).
    upload=False points at objects that are already there (re-processing, rehearsals)."""

    def __init__(self, bucket, user_id: str, source: str, run_id: str | None = None, upload: bool = True) -> None:
        self.bucket, self.user_id, self.source, self.run_id, self.upload = bucket, user_id, source, run_id, upload
        self.rows: list[dict] = []

    def path(self, path: str) -> str:
        return f"users/{self.user_id}/{path}"

    def put_bytes(self, path: str, content: bytes, content_type: str, endpoint: str, day: str | None = None) -> str | None:
        if self.bucket is None:
            return None
        full = self.path(path)
        ref = f"gs://{self.bucket.name}/{full}"
        if not self.upload:
            return ref
        self.bucket.blob(full).upload_from_string(content, content_type=content_type)
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
