"""Keep secrets out of the logs.

Libraries put credentials into log lines and exception texts (httpx logs Telegram
URLs with the bot token, python-telegram-bot puts the token into InvalidToken).
This filter rewrites every log record, including tracebacks, before any handler
formats it. It is attached to every handler of the root and uvicorn loggers.
"""

from __future__ import annotations

import logging
import os
import re

SECRET_ENV = ("TELEGRAM_BOT_TOKEN", "ANTHROPIC_API_KEY")
MASK = "<redacted>"

# Shapes of secrets even if the value differs from the env (e.g. a mistyped or older token).
PATTERNS = [
    re.compile(r"\d{5,}:[A-Za-z0-9_-]{30,}"),   # Telegram bot token
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),   # Anthropic API key
]


def redact(text: str) -> str:
    for name in SECRET_ENV:
        value = os.environ.get(name, "").strip()
        if len(value) >= 8:
            text = text.replace(value, MASK)
            secret_part = value.split(":", 1)[-1]  # a token with a mangled bot id still has this part
            if len(secret_part) >= 20:
                text = text.replace(secret_part, MASK)
    for p in PATTERNS:
        text = p.sub(MASK, text)
    return text


def _clean(arg):
    """Numbers stay numbers (for %d); anything else (strings, exceptions, URLs) is redacted text."""
    if arg is None or isinstance(arg, (bool, int, float)):
        return arg
    return redact(str(arg))


class RedactSecrets(logging.Filter):
    _fmt = logging.Formatter()

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            # Keep msg/args shape: some formatters (uvicorn access log) unpack record.args.
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
            if isinstance(record.args, tuple):
                record.args = tuple(_clean(a) for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: _clean(v) for k, v in record.args.items()}
            if record.exc_info and not record.exc_text:
                record.exc_text = self._fmt.formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = redact(record.exc_text)
            if record.stack_info:
                record.stack_info = redact(record.stack_info)
        except Exception:  # noqa: BLE001 - a broken filter must never drop the log line
            pass
        return True


_FILTER = RedactSecrets()


def install(*logger_names: str) -> None:
    """Attach the filter to every handler of the given loggers (root and uvicorn by default)."""
    names = logger_names or ("", "uvicorn", "uvicorn.error", "uvicorn.access")
    for name in names:
        for handler in logging.getLogger(name).handlers:
            if _FILTER not in handler.filters:
                handler.addFilter(_FILTER)
