"""Главный модуль запуска и управления жизненным циклом Telegram-бота."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from config import Config, load_config
from src.handlers import admin
from src.middleware.db_middleware import DbSessionMiddleware
from src.middleware.subscription_gate import SubscriptionGateMiddleware
from src.services.scheduler import run_periodic

__all__ = ["main"]


async def main() -> None:
    """Точка входа: инициализация компонентов и запуск polling бота."""
    config: Config = load_config()

    level = getattr(logging, str(config.log.level).upper(), logging.INFO)
    logging.basicConfig(level=level, format=config.log.format)

    bot = Bot(
        token=config.bot.token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dp = Dispatcher()

    db_middleware = DbSessionMiddleware()
    dp.message.middleware(db_middleware)
    dp.callback_query.middleware(db_middleware)

    gate = SubscriptionGateMiddleware()
    dp.message.middleware(gate)
    dp.callback_query.middleware(gate)

    # admin.router объединяет маршруты student, queue, subscription, vip
    dp.include_router(admin.router)

    scheduler_task = asyncio.create_task(run_periodic(bot))

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
