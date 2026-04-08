"""Блокировка команд при истёкшей подписке чата (кроме start, help, оплаты)."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from src.db.db import get_db
from src.db import queries as Q
from src.services.subscription import effective_access, has_base_features

EXPIRED_TEXT = (
    "❌ У этого чата закончился пробный период или подписка.\n"
    "Доступны только /help, /start и /pay (оплата).\n"
    "Продлите доступ: /pay"
)


def _message_allowlisted(m: Message) -> bool:
    if not m.text:
        return False
    first = m.text.strip().split()[0]
    base = first.split("@")[0].lower()
    if base in ("/start", "/help", "/pay", "/sub", "/swap"):
        return True
    return False


def _callback_allowlisted(cq: CallbackQuery) -> bool:
    d = cq.data or ""
    return (
        d.startswith("pay|")
        or d.startswith("chk|")
        or d == "pay_info"
        or d == "pay_cancel"
        or d.startswith("swp|")
        or d.startswith("swu|")
    )


class SubscriptionGateMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat_id: int | None = None
        if isinstance(event, Message):
            if _message_allowlisted(event):
                return await handler(event, data)
            chat_id = event.chat.id
        elif isinstance(event, CallbackQuery):
            if _callback_allowlisted(event):
                return await handler(event, data)
            if event.message:
                chat_id = event.message.chat.id
            else:
                return await handler(event, data)
        else:
            return await handler(event, data)

        if chat_id is None:
            return await handler(event, data)

        with get_db() as db:
            chat = Q.get_chat(db, chat_id)
            if chat is None:
                return await handler(event, data)
            acc = effective_access(chat)
            if has_base_features(acc):
                return await handler(event, data)

        if isinstance(event, Message):
            await event.answer(EXPIRED_TEXT)
        else:
            await event.answer(EXPIRED_TEXT[:200], show_alert=True)
        return None
