"""Asking the AI model: prompts, the request, the answer.

Vendor-neutral: the request goes through the `LLM` port. Prompts are in English; the answer
language is set per request.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from secondwind.core.i18n import language_name
from secondwind.core.models import DEFAULT_MODE, effort_for, model_override, tier_for
from secondwind.core.ports import LLM, LLMRequest

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
