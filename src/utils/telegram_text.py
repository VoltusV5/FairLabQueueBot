"""Разбор текста сообщения без блоков text_mention."""

from __future__ import annotations

from aiogram.enums import MessageEntityType
from aiogram.types import Message


def text_without_text_mentions(message: Message) -> str:
    t = message.text or ""
    ents = sorted(
        [
            e
            for e in (message.entities or [])
            if e.type == MessageEntityType.TEXT_MENTION
        ],
        key=lambda x: -x.offset,
    )
    for e in ents:
        t = t[: e.offset] + " " + t[e.offset + e.length :]
    return " ".join(t.split())
