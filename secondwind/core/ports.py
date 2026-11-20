"""Interfaces between the core and the outside world.

The core and the apps talk to storage, the data warehouse and the AI model only through
these protocols. Each one has adapters in `secondwind.adapters` (GCP and Anthropic today);
another cloud or AI vendor means a new adapter, not changes in the core.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Protocol

# ---------- bot state (profile, goal, settings, pending input, AI usage) ----------


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


# ---------- data warehouse: named read queries for the bot ----------


class DataReader(Protocol):
    def get_last_activity(self) -> dict | None: ...
    def get_week_activities(self, weeks_back: int = 0) -> list[dict]: ...
    def get_health_last_days(self, days: int = 14) -> list[dict]: ...
    def get_load_last_days(self, days: int = 1) -> list[dict]: ...
    def get_records(self, since: date) -> list[dict]: ...
    def get_period(self, start: date, end: date) -> dict: ...
    def get_weekly_run_km(self, weeks: int = 16) -> list[dict]: ...
    def get_load_series(self, days: int = 120) -> list[dict]: ...
    def get_health_series(self, days: int = 90) -> list[dict]: ...
    def get_run_efficiency(self, days: int = 180) -> list[dict]: ...
    def get_athlete_inputs(self) -> dict: ...
    def get_source_status(self) -> list[dict]: ...


# ---------- data warehouse: writes and sync cursors for the collector ----------


class DataWriter(Protocol):
    def upsert(self, table_name: str, rows: list[dict]) -> int: ...
    def stored_file_ids(self, source: str, start: str) -> set[str]: ...


class IngestStore(Protocol):
    def cursor(self, source: str) -> date | None: ...
    def record(self, source: str, cursor: date | None, status: str, error: str | None = None) -> None: ...


# ---------- AI model ----------

Tier = Literal["small", "medium", "large"]


@dataclass
class Block:
    """A piece of the system prompt. `cache=True` asks the vendor to cache everything up to it."""
    text: str
    cache: bool = False


@dataclass
class LLMRequest:
    tier: Tier
    messages: list[dict]                  # [{"role": "user" | "assistant", "content": str}]
    system: list[Block] = field(default_factory=list)
    model: str | None = None              # explicit model set at deploy time; overrides the tier
    effort: str | None = None
    max_tokens: int = 16000
    task: str = ""                        # for logs only


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    stop_reason: str | None = None
    refused: bool = False
    refusal_details: object = None


class LLM(Protocol):
    def complete(self, req: LLMRequest) -> LLMResponse: ...
