"""Which Claude model does which job.

Users never see model names. Whoever deploys the bot can override any task with
an env var MODEL_<TASK> (for example MODEL_CHAT=claude-opus-5-5), set from the
`bot_models` Terraform variable.
"""

from __future__ import annotations

import os

# task -> (default model, effort)
TASKS = {
    "chat":     ("claude-sonnet-5-5", "low"),     # free-form questions
    "analysis": ("claude-sonnet-5-5", "medium"),  # /coach /health /nutrition
    "weekly":   ("claude-haiku-5-5", "low"),      # weekly summary (stage 5)
    "alerts":   ("claude-haiku-5-5", "low"),      # health alerts (stage 5)
    "morning":  ("claude-haiku-5-5", "low"),      # optional morning report (stage 5)
}

# Models that accept the server-side refusal fallback (`fallbacks: "default"`).
_FALLBACK_PREFIXES = ("claude-sonnet-5-5", "claude-opus-5", "claude-fable-5")


def model_for(task: str) -> str:
    return os.environ.get(f"MODEL_{task.upper()}", "").strip() or TASKS[task][0]


def effort_for(task: str) -> str:
    return os.environ.get(f"EFFORT_{task.upper()}", "").strip() or TASKS[task][1]


def supports_fallback(model: str) -> bool:
    return model.startswith(_FALLBACK_PREFIXES)
