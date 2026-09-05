"""Тесты для точки входа и жизненного цикла бота (bot.py)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import bot
import pytest
from config import Config, LogSettings, TgBot


@pytest.mark.asyncio
async def test_bot_main_lifecycle() -> None:
    """Проверка полного цикла инициализации и корректного завершения main()."""
    mock_config = Config(
        bot=TgBot(token="123456:FAKE_TOKEN", admin_ids=[123]),
        log=LogSettings(level="INFO", format="%(message)s"),
        yookassa=None,
    )

    mock_bot = MagicMock()
    mock_bot.session = MagicMock()
    mock_bot.session.close = AsyncMock()
    mock_bot.delete_webhook = AsyncMock()

    mock_dp = MagicMock()
    mock_dp.message = MagicMock()
    mock_dp.message.middleware = MagicMock()
    mock_dp.callback_query = MagicMock()
    mock_dp.callback_query.middleware = MagicMock()
    mock_dp.include_router = MagicMock()
    mock_dp.start_polling = AsyncMock()

    async def fake_run_periodic(_b: object) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass

    with (
        patch("bot.load_config", return_value=mock_config),
        patch("bot.Bot", return_value=mock_bot),
        patch("bot.Dispatcher", return_value=mock_dp),
        patch("bot.run_periodic", side_effect=fake_run_periodic),
    ):
        await bot.main()

    # Проверка вызовов конфигурации и бота
    mock_bot.delete_webhook.assert_awaited_once_with(drop_pending_updates=True)
    mock_dp.start_polling.assert_awaited_once_with(mock_bot)

    # Проверка регистрации middleware (DbSessionMiddleware и SubscriptionGate)
    assert mock_dp.message.middleware.call_count == 2
    assert mock_dp.callback_query.middleware.call_count == 2

    # Проверка включения маршрутизатора
    mock_dp.include_router.assert_called_once()

    # Проверка гарантированного закрытия сессии в блоке finally
    mock_bot.session.close.assert_awaited_once()
