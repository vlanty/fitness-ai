"""Calls to Claude. Prompts are in English; the answer language is set per request."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import anthropic

from i18n import language_name
from models import DEFAULT_MODE, cost_usd, effort_for, model_for, supports_fallback

logger = logging.getLogger(__name__)

# Answers are shown in Telegram: we convert this small Markdown subset to Telegram HTML.
FORMAT_NOTE = """Formatting (the answer is shown in a Telegram chat):
- Start each section with a short bold heading on its own line, like **Recovery**; no # headings.
- Short paragraphs and "- " bullet lists; bold only key numbers or conclusions.
- No tables, no code blocks, no horizontal rules."""

# The bot's persona. The project is "Second Wind"; the bot introduces itself as Vita.
PERSONA = ("Your name is Vita (Вита in Russian), the personal assistant of the Second Wind project. "
           "You are female: in Russian always use feminine forms about yourself "
           "(«я посмотрела», «я заметила», «я рада»). Do not introduce yourself unless asked.")

GOAL_NOTE = ("The athlete has no specific race goal set: treat this as ongoing training "
             "(consistency, general fitness, staying healthy).")  # used when no athlete note is given

COACH_PROMPT = """{persona}
In this answer you act as an experienced running and triathlon coach with 15 years of work with amateur athletes.
Analyze the data concretely: what is good, what is bad, what to change. No filler, no generic advice.
{goal}

Data:
{data}

{format}

Answer in {language}, up to 400 words."""

HEALTH_PROMPT = """{persona}
In this answer you act as a sports physician reviewing an amateur athlete's physiological data.

Pay special attention to:
- HRV trend: a drop of more than 10% below baseline flags incomplete recovery
- Resting heart rate: a rise of 5+ bpm above baseline may mean illness or overtraining
- Sleep: under 7 hours or a score below 60 is a problem
- Body Battery: persistently low means accumulated fatigue

If you see worrying patterns, say so directly.
{goal}

Data:
{data}

{format}

Answer in {language}, up to 400 words."""

NUTRITION_PROMPT = """{persona}
In this answer you act as a sports nutritionist. Give concrete recommendations with numbers:
- Calories on training days and on recovery days
- What to eat 2–3 hours before a workout and within 30 minutes after
- Hydration (ml per hour for this type of training)
- For long workouts, a fueling strategy during the session
Base calorie numbers on the athlete profile when it is given; if weight is missing, say what you assumed.
{goal}

Training and load data:
{data}

{format}

Answer in {language}, up to 400 words."""

ASK_PROMPT = """{persona}
You are the personal coach and sports physician of an amateur athlete. Answer their question using their data.
Rely only on the data below. If the data needed is missing or stale, say so plainly; do not make anything up.
{goal}

Question: {question}

Data:
{data}

{format}

Answer in the language the question is written in (if that is unclear, in {language}), to the point, up to 250 words."""


_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


class Refused(Exception):
    """Claude (and any fallback model) declined the request."""


@dataclass
class Answer:
    text: str
    model: str
    input_tokens: int
    output_tokens: int

    @property
    def cost_usd(self) -> float:
        return cost_usd(self.model, self.input_tokens, self.output_tokens)


def analyze(prompt_template: str, data_summary: str, task: str, lang: str,
            mode: str = DEFAULT_MODE, athlete: str | None = None, **fmt) -> Answer:
    model = model_for(task, mode)
    params = dict(
        model=model,
        max_tokens=16000,
        output_config={"effort": effort_for(task)},
        messages=[{"role": "user", "content": prompt_template.format(
            data=data_summary, language=language_name(lang), goal=athlete or GOAL_NOTE, format=FORMAT_NOTE, persona=PERSONA, **fmt)}],
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
    text = "\n".join(b.text for b in response.content if b.type == "text").strip()
    return Answer(text, response.model, response.usage.input_tokens, response.usage.output_tokens)
