"""`StateStore` in memory: for tests (and a base for a future self-hosted "lite" variant)."""

from __future__ import annotations

import copy


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
