"""`StateStore` in memory: for tests (and a base for a future self-hosted "lite" variant)."""

from __future__ import annotations

import copy


class MemoryStore:
    def __init__(self) -> None:
        self.users: dict[str, dict] = {}
        self.goals: dict[str, dict] = {}
        self.usage: dict[tuple[str, str], dict] = {}
        self.identities: dict[str, str] = {}

    def _user(self, user_id):
        return self.users.setdefault(user_id, {"profile": {}, "settings": {}})

    def get_user(self, user_id):
        return copy.deepcopy(self.users.get(user_id, {}))

    def update_profile(self, user_id, fields):
        prof = self._user(user_id)["profile"]
        for k, v in fields.items():
            if v is None:
                prof.pop(k, None)
            else:
                prof[k] = v

    def clear_profile(self, user_id):
        self._user(user_id)["profile"] = {}

    def update_settings(self, user_id, fields):
        self._user(user_id)["settings"].update(fields)

    def set_pending(self, user_id, pending):
        if pending is None:
            self._user(user_id).pop("pending", None)
        else:
            self._user(user_id)["pending"] = copy.deepcopy(pending)

    def get_goal(self, user_id):
        return copy.deepcopy(self.goals.get(user_id))

    def set_goal(self, user_id, goal):
        self.goals[user_id] = copy.deepcopy(goal)

    def delete_goal(self, user_id):
        self.goals.pop(user_id, None)

    def add_usage(self, user_id, month, input_tokens, output_tokens, cost_usd):
        u = self.usage.setdefault((user_id, month), {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
        u["calls"] += 1
        u["input_tokens"] += input_tokens
        u["output_tokens"] += output_tokens
        u["cost_usd"] += cost_usd

    def get_usage(self, user_id, month):
        return dict(self.usage.get((user_id, month), {}))

    def get_identity(self, key):
        return self.identities.get(key)

    def link_identity(self, key, user_id):
        self.identities[key] = user_id
