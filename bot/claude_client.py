"""Calls to Claude. Prompts are in English; the answer language is set per request."""

from __future__ import annotations

import logging
import os

import anthropic

from i18n import language_name
from models import effort_for, model_for, supports_fallback

logger = logging.getLogger(__name__)

# Answers are shown in Telegram: we convert this small Markdown subset to Telegram HTML.
FORMAT_NOTE = """Formatting (the answer is shown in a Telegram chat):
- Start each section with a short bold heading on its own line, like **Recovery**; no # headings.
- Short paragraphs and "- " bullet lists; bold only key numbers or conclusions.
- No tables, no code blocks, no horizontal rules."""

GOAL_NOTE = ("The athlete has no specific race goal set: treat this as ongoing training "
             "(consistency, general fitness, staying healthy).")

COACH_PROMPT = """You are an experienced running and triathlon coach with 15 years of work with amateur athletes.
Analyze the data concretely: what is good, what is bad, what to change. No filler, no generic advice.
{goal}

Data:
{data}

{format}

Answer in {language}, up to 400 words."""

HEALTH_PROMPT = """You are a sports physician reviewing an amateur athlete's physiological data.

Pay special attention to:
- HRV trend: a drop of more than 10% below baseline flags incomplete recovery
- Resting heart rate: a rise of 5+ bpm above baseline may mean illness or overtraining
- Sleep: under 7 hours or a score below 60 is a problem
- Body Battery: persistently low means accumulated fatigue

If you see worrying patterns, say so directly.

Data:
{data}

{format}

Answer in {language}, up to 400 words."""

NUTRITION_PROMPT = """You are a sports nutritionist. Give concrete recommendations with numbers:
- Calories on training days and on recovery days
- What to eat 2–3 hours before a workout and within 30 minutes after
- Hydration (ml per hour for this type of training)
- For long workouts, a fueling strategy during the session

Training and load data:
{data}

{format}

Answer in {language}, up to 400 words."""

ASK_PROMPT = """You are the personal coach and sports physician of an amateur athlete. Answer their question using their data.
Rely only on the data below. If the data needed is missing or stale, say so plainly; do not make anything up.
{goal}

Question: {question}

Data:
{data}

{format}

Answer in {language}, to the point, up to 250 words."""


_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


class Refused(Exception):
    """Claude (and any fallback model) declined the request."""


def analyze(prompt_template: str, data_summary: str, task: str, lang: str, **fmt) -> str:
    model = model_for(task)
    params = dict(
        model=model,
        max_tokens=16000,
        output_config={"effort": effort_for(task)},
        messages=[{"role": "user", "content": prompt_template.format(
            data=data_summary, language=language_name(lang), goal=GOAL_NOTE, format=FORMAT_NOTE, **fmt)}],
    )
    client = _get_client()
    if supports_fallback(model):
        response = client.beta.messages.create(
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **params)
    else:
        response = client.messages.create(**params)

    if response.stop_reason == "refusal":
        raise Refused(getattr(response, "stop_details", None))
    if response.stop_reason == "max_tokens":
        logger.warning("Claude answer cut by max_tokens (task=%s, model=%s)", task, model)
    logger.info("claude task=%s model=%s in=%s out=%s", task, response.model,
                response.usage.input_tokens, response.usage.output_tokens)
    return "\n".join(b.text for b in response.content if b.type == "text").strip()
