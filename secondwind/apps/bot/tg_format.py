"""Turn Claude's Markdown into Telegram HTML.

Claude writes **bold**, ## headings and "- " lists. Telegram shows them as raw
asterisks and hashes unless the message is sent with a parse mode, and its own
Markdown modes break on unescaped characters. HTML is the most forgiving: we
escape everything first, then add a small set of tags Telegram supports.
"""

from __future__ import annotations

import html
import re

TELEGRAM_LIMIT = 4096

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+")
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC = re.compile(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])|(?<![\w_])_(?!\s)([^_\n]+?)(?<!\s)_(?![\w_])")


def _inline(s: str) -> str:
    s = _CODE.sub(r"<code>\1</code>", s)
    s = _BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", s)
    s = _ITALIC.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", s)
    return s


def md_to_html(text: str) -> str:
    out = []
    for line in html.escape(text, quote=False).splitlines():
        if _RULE.match(line):
            out.append("")
            continue
        m = _HEADING.match(line)
        if m:
            title = m.group(1).replace("**", "").replace("__", "")
            out.append(f"<b>{_inline(title)}</b>")
            continue
        line = _BULLET.sub(lambda b: b.group(1) + "• ", line)
        out.append(_inline(line))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def html_to_plain(text: str) -> str:
    """Plain-text fallback for one chunk if Telegram rejects its HTML."""
    return html.unescape(re.sub(r"</?(?:b|i|code)>", "", text))


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split on paragraph, then line boundaries so no chunk exceeds Telegram's limit."""
    chunks, cur = [], ""
    for para in text.split("\n\n"):
        piece = para if not cur else "\n\n" + para
        if len(cur) + len(piece) <= limit:
            cur += piece
            continue
        if cur:
            chunks.append(cur)
        cur = ""
        for line in para.split("\n"):
            piece = line if not cur else "\n" + line
            if len(cur) + len(piece) <= limit:
                cur += piece
            else:
                if cur:
                    chunks.append(cur)
                cur = line[:limit]
    if cur:
        chunks.append(cur)
    return chunks
