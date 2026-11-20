"""Who is this chat? Maps a Telegram chat to an internal user_id.

TENANCY=solo (the only mode today): one owner. The chat in TELEGRAM_CHAT_ID belongs to the
user in OWNER_USER_ID; every other chat is rejected.
TENANCY=multi (planned): users are created at onboarding and linked to chats through
`identities/telegram:<chat_id>` in the state store.
"""

from __future__ import annotations

import os


def mode() -> str:
    return os.environ.get("TENANCY", "solo").strip() or "solo"


def owner_chat() -> str:
    return os.environ.get("TELEGRAM_CHAT_ID", "").strip()


def owner_user_id() -> str:
    return os.environ.get("OWNER_USER_ID", "").strip()


def identity_key(chat_id: int | str) -> str:
    return f"telegram:{chat_id}"


def user_for_chat(chat_id: int | str | None) -> str | None:
    """The user_id behind a chat, or None if the chat may not use the bot."""
    if chat_id is None:
        return None
    if mode() != "solo":
        raise NotImplementedError(f"TENANCY={mode()} is not supported yet")
    owner, uid = owner_chat(), owner_user_id()
    return uid if owner and uid and str(chat_id) == owner else None
