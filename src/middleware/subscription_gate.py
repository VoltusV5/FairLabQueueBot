"""Блокировка команд при истёкшей подписке чата (кроме start, help, оплаты)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.repositories.chat import get_chat
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
        or d.startswith("help_tier|")
    )


class SubscriptionGateMiddleware(BaseMiddleware):
    """Middleware для проверки наличия активной подписки чата перед обработкой.

    Пропускает белые списки команд (/help, /start, /pay) и соответствующие
    callback-запросы. Для остальных запросов блокирует доступ при истечении.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Перехватывает и валидирует событие на предмет подписки чата.

        Args:
            handler: Следующий обработчик в цепочке.
            event: Входящее событие Telegram.
            data: Контекстные данные обработчика.

        Returns:
            Any: Результат выполнения обработчика.
        """
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

        session: AsyncSession | None = data.get("session")
        if not session:
            return await handler(event, data)

        chat = await get_chat(session, chat_id)

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
