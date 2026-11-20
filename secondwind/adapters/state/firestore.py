"""Bot state: profile, goal, settings, pending input, Claude usage.

The bot talks to `StateStore` only. `FirestoreStore` is the GCP implementation;
`MemoryStore` is for tests (and a base for a future self-hosted "lite" variant).

Layout (Firestore):
  users/{chat_id}                  profile{...}, settings{...}, pending{...}
  users/{chat_id}/goals/active     the current goal (one at a time)
  users/{chat_id}/usage/{YYYY-MM}  calls, input_tokens, output_tokens, cost_usd
"""

from __future__ import annotations

import copy
import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class StateStore(Protocol):
    def get_user(self, chat_id: int) -> dict: ...
    def update_profile(self, chat_id: int, fields: dict) -> None: ...
    def clear_profile(self, chat_id: int) -> None: ...
    def update_settings(self, chat_id: int, fields: dict) -> None: ...
    def set_pending(self, chat_id: int, pending: dict | None) -> None: ...
    def get_goal(self, chat_id: int) -> dict | None: ...
    def set_goal(self, chat_id: int, goal: dict) -> None: ...
    def delete_goal(self, chat_id: int) -> None: ...
    def add_usage(self, chat_id: int, month: str, input_tokens: int, output_tokens: int, cost_usd: float) -> None: ...
    def get_usage(self, chat_id: int, month: str) -> dict: ...


class MemoryStore:
    def __init__(self) -> None:
        self.users: dict[int, dict] = {}
        self.goals: dict[int, dict] = {}
        self.usage: dict[tuple[int, str], dict] = {}

    def _user(self, chat_id):
        return self.users.setdefault(chat_id, {"profile": {}, "settings": {}})

    def get_user(self, chat_id):
        return copy.deepcopy(self.users.get(chat_id, {}))

    def update_profile(self, chat_id, fields):
        prof = self._user(chat_id)["profile"]
        for k, v in fields.items():
            if v is None:
                prof.pop(k, None)
            else:
                prof[k] = v

    def clear_profile(self, chat_id):
        self._user(chat_id)["profile"] = {}

    def update_settings(self, chat_id, fields):
        self._user(chat_id)["settings"].update(fields)

    def set_pending(self, chat_id, pending):
        if pending is None:
            self._user(chat_id).pop("pending", None)
        else:
            self._user(chat_id)["pending"] = copy.deepcopy(pending)

    def get_goal(self, chat_id):
        return copy.deepcopy(self.goals.get(chat_id))

    def set_goal(self, chat_id, goal):
        self.goals[chat_id] = copy.deepcopy(goal)

    def delete_goal(self, chat_id):
        self.goals.pop(chat_id, None)

    def add_usage(self, chat_id, month, input_tokens, output_tokens, cost_usd):
        u = self.usage.setdefault((chat_id, month), {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
        u["calls"] += 1
        u["input_tokens"] += input_tokens
        u["output_tokens"] += output_tokens
        u["cost_usd"] += cost_usd

    def get_usage(self, chat_id, month):
        return dict(self.usage.get((chat_id, month), {}))


class FirestoreStore:
    def __init__(self, project: str, client=None) -> None:
        from google.cloud import firestore  # imported lazily: tests don't need the package

        self._fs = firestore
        self.db = client or firestore.Client(project=project)

    def _doc(self, chat_id):
        return self.db.collection("users").document(str(chat_id))

    def get_user(self, chat_id):
        snap = self._doc(chat_id).get()
        return (snap.to_dict() or {}) if snap.exists else {}

    def update_profile(self, chat_id, fields):
        data = {f"profile.{k}": (self._fs.DELETE_FIELD if v is None else v) for k, v in fields.items()}
        self._update(chat_id, data)

    def clear_profile(self, chat_id):
        self._update(chat_id, {"profile": {}})

    def update_settings(self, chat_id, fields):
        self._update(chat_id, {f"settings.{k}": v for k, v in fields.items()})

    def set_pending(self, chat_id, pending):
        self._update(chat_id, {"pending": self._fs.DELETE_FIELD if pending is None else pending})

    def _update(self, chat_id, data):
        doc = self._doc(chat_id)
        try:
            doc.update(data)
        except Exception as e:  # NotFound on the first write for this user
            if type(e).__name__ != "NotFound":
                raise
            doc.set({"profile": {}, "settings": {}})
            doc.update(data)

    def get_goal(self, chat_id):
        snap = self._doc(chat_id).collection("goals").document("active").get()
        return snap.to_dict() if snap.exists else None

    def set_goal(self, chat_id, goal):
        self._doc(chat_id).collection("goals").document("active").set(goal)

    def delete_goal(self, chat_id):
        self._doc(chat_id).collection("goals").document("active").delete()

    def add_usage(self, chat_id, month, input_tokens, output_tokens, cost_usd):
        inc = self._fs.Increment
        self._doc(chat_id).collection("usage").document(month).set({
            "calls": inc(1), "input_tokens": inc(input_tokens),
            "output_tokens": inc(output_tokens), "cost_usd": inc(cost_usd),
        }, merge=True)

    def get_usage(self, chat_id, month):
        snap = self._doc(chat_id).collection("usage").document(month).get()
        return (snap.to_dict() or {}) if snap.exists else {}
