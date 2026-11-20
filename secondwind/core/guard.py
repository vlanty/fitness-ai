"""Per-user guard state for the filter (architecture 2.1): repeats and the model-call rate.

The state is a small dict kept with the user (StateStore.update_guard):
  {"last_hash": str, "last_at": iso, "model_calls": [iso, ...]}  (calls of the last hour only)
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta

REPEAT_WINDOW = timedelta(minutes=2)
RATE_WINDOW = timedelta(hours=1)


def _hash(text: str) -> str:
    norm = re.sub(r"[\W_]+", " ", (text or "").lower()).strip()
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


def _ts(v: str) -> datetime:
    return datetime.fromisoformat(v)


def is_repeat(guard: dict, text: str, now: datetime) -> bool:
    last = guard.get("last_at")
    return bool(last) and guard.get("last_hash") == _hash(text) and now - _ts(last) < REPEAT_WINDOW


def record_message(guard: dict, text: str, now: datetime) -> dict:
    return {**guard, "last_hash": _hash(text), "last_at": now.isoformat()}


def _recent(guard: dict, now: datetime) -> list[str]:
    return [c for c in guard.get("model_calls") or [] if now - _ts(c) < RATE_WINDOW]


def model_allowed(guard: dict, now: datetime, limit: int) -> bool:
    return len(_recent(guard, now)) < limit


def record_model_call(guard: dict, now: datetime) -> dict:
    return {**guard, "model_calls": _recent(guard, now) + [now.isoformat()]}
