"""Which model tier does which job.

The core thinks in tiers (small / medium / large); the AI adapter maps each tier to a
model of its vendor (see `secondwind.adapters.llm`).

Users never see model names: they pick a mode in /settings (economy / normal / max).
Whoever deploys the bot can pin the "normal" model of any task with an env var
MODEL_<TASK> (for example MODEL_CHAT=claude-opus-5-5), set from the `bot_models`
Terraform variable, and its effort with EFFORT_<TASK>.
"""

from __future__ import annotations

import os

from secondwind.core.ports import Tier

# task -> (default tier, effort)
TASKS: dict[str, tuple[Tier, str]] = {
    "chat":     ("medium", "low"),     # free-form questions
    "analysis": ("medium", "medium"),  # /coach /health /nutrition
    "weekly":   ("small", "low"),      # weekly summary (stage 5)
    "alerts":   ("small", "low"),      # health alerts (stage 5)
    "morning":  ("small", "low"),      # optional morning report (stage 5)
}

MODES = ("economy", "normal", "max")
DEFAULT_MODE = "normal"
# Tasks the mode switch applies to; routine messages always use their own cheap tier.
MODE_TASKS = ("chat", "analysis")
MODE_TIERS: dict[str, Tier] = {"economy": "small", "max": "large"}


def _mode_switched(task: str, mode: str) -> bool:
    return task in MODE_TASKS and mode in MODE_TIERS


def tier_for(task: str, mode: str = DEFAULT_MODE) -> Tier:
    return MODE_TIERS[mode] if _mode_switched(task, mode) else TASKS[task][0]


def model_override(task: str, mode: str = DEFAULT_MODE) -> str | None:
    """The model pinned at deploy time for this task, unless the user's mode switches it."""
    if _mode_switched(task, mode):
        return None
    return os.environ.get(f"MODEL_{task.upper()}", "").strip() or None


def effort_for(task: str) -> str:
    return os.environ.get(f"EFFORT_{task.upper()}", "").strip() or TASKS[task][1]
