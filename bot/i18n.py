"""Bot texts in several languages.

Each language is one file in locales/<code>.json. To add a language, copy en.json,
translate the values and keep the keys. Missing keys fall back to English.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT = "en"
LANGUAGE_NAMES = {"en": "English", "ru": "Russian"}  # for the "answer in <language>" prompt line

_LOCALES: dict[str, dict[str, str]] = {
    p.stem: json.loads(p.read_text(encoding="utf-8"))
    for p in sorted((Path(__file__).parent / "locales").glob("*.json"))
}
SUPPORTED = sorted(_LOCALES)


def resolve(language_code: str | None) -> str:
    """Telegram language_code ('ru', 'en-US', ...) -> a supported locale, English otherwise."""
    code = (language_code or "").split("-")[0].lower()
    return code if code in _LOCALES else DEFAULT


def t(lang: str, key: str, **kw) -> str:
    text = _LOCALES.get(lang, {}).get(key) or _LOCALES[DEFAULT].get(key)
    if text is None:
        logger.warning("missing translation key %s", key)
        return key
    return text.format(**kw) if kw else text


def language_name(lang: str) -> str:
    return LANGUAGE_NAMES.get(lang, lang)
