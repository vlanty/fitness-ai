"""Asking the AI model: prompts, the request, the answer.

Vendor-neutral: the request goes through the `LLM` port. Prompts are in English; the answer
language is set per request.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from secondwind.core.i18n import language_name
from secondwind.core.models import DEFAULT_MODE, effort_for, model_override, tier_for
from secondwind.core.ports import LLM, LLMRequest

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"


def _prompt(name: str) -> str:
    """A prompt file from core/prompts/ (the trailing newline of the file is not part of it)."""
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").removesuffix("\n")


# Templates use str.format fields: {persona} {goal} {data} {format} {language} (+ {question} in ask).
# The answer is shown in Telegram: format.md asks for the Markdown subset we convert to Telegram HTML.
FORMAT_NOTE = _prompt("format")
# The bot's persona. The project is "Second Wind"; the bot introduces itself as Vita.
PERSONA = _prompt("persona")
GOAL_NOTE = _prompt("no_goal")  # used when no athlete note is given
COACH_PROMPT = _prompt("coach")
HEALTH_PROMPT = _prompt("health")
NUTRITION_PROMPT = _prompt("nutrition")
ASK_PROMPT = _prompt("ask")


class Refused(Exception):
    """The model (and any fallback model) declined the request."""


@dataclass
class Answer:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


def build_request(prompt_template: str, data_summary: str, task: str, lang: str,
                  mode: str = DEFAULT_MODE, athlete: str | None = None, **fmt) -> LLMRequest:
    content = prompt_template.format(data=data_summary, language=language_name(lang), goal=athlete or GOAL_NOTE,
                                     format=FORMAT_NOTE, persona=PERSONA, **fmt)
    return LLMRequest(tier=tier_for(task, mode), model=model_override(task, mode), effort=effort_for(task),
                      messages=[{"role": "user", "content": content}], task=task)


def analyze(llm: LLM, prompt_template: str, data_summary: str, task: str, lang: str,
            mode: str = DEFAULT_MODE, athlete: str | None = None, **fmt) -> Answer:
    r = llm.complete(build_request(prompt_template, data_summary, task, lang, mode, athlete, **fmt))
    if r.refused:
        raise Refused(r.refusal_details)
    if r.stop_reason == "max_tokens":
        logger.warning("model answer cut by max_tokens (task=%s, model=%s)", task, r.model)
    logger.info("llm task=%s model=%s in=%s out=%s", task, r.model, r.input_tokens, r.output_tokens)
    return Answer(r.text, r.model, r.input_tokens, r.output_tokens, r.cost_usd)
