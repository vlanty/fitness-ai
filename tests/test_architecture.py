"""The core must not depend on clouds, AI vendors, Telegram or adapters (see core/ports.py)."""

import re
from pathlib import Path

CORE = Path(__file__).resolve().parent.parent / "secondwind" / "core"
FORBIDDEN = re.compile(r"^\s*(from|import)\s+(secondwind\.(adapters|apps)|google|anthropic|openai|telegram|matplotlib)\b",
                       re.M)


def test_core_has_no_outside_dependencies():
    bad = [f"{p.name}: {m.group(0).strip()}" for p in CORE.rglob("*.py") for m in FORBIDDEN.finditer(p.read_text())]
    assert bad == []
