"""Which Claude model does which job, and what it costs.

Users never see model names: they pick a mode in /settings (economy / normal / max).
Whoever deploys the bot can override the "normal" model of any task with an env var
MODEL_<TASK> (for example MODEL_CHAT=claude-opus-5-5), set from the `bot_models`
Terraform variable.
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

MODES = ("economy", "normal", "max")
DEFAULT_MODE = "normal"
# Tasks the mode switch applies to; routine messages always use their own cheap model.
MODE_TASKS = ("chat", "analysis")
MODE_MODELS = {"economy": "claude-haiku-5-5", "max": "claude-opus-5-5"}

# USD per 1M tokens (input, output), Anthropic API list prices.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}

# Models that accept the server-side refusal fallback (`fallbacks: "default"`).
_FALLBACK_PREFIXES = ("claude-sonnet-5-5", "claude-opus-5", "claude-fable-5")


def model_for(task: str, mode: str = DEFAULT_MODE) -> str:
    if task in MODE_TASKS and mode in MODE_MODELS:
        return MODE_MODELS[mode]
    return os.environ.get(f"MODEL_{task.upper()}", "").strip() or TASKS[task][0]


def effort_for(task: str) -> str:
    return os.environ.get(f"EFFORT_{task.upper()}", "").strip() or TASKS[task][1]


def supports_fallback(model: str) -> bool:
    return model.startswith(_FALLBACK_PREFIXES)


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """List-price cost of one call; 0 for a model missing from PRICES (tokens are still counted)."""
    for name, (p_in, p_out) in PRICES.items():
        if model.startswith(name):
            return (input_tokens * p_in + output_tokens * p_out) / 1_000_000
    return 0.0
