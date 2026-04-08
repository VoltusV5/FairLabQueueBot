"""Главный файл с инициализацией бота."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from config import Config, load_config
from src.handlers import admin
from src.middleware.subscription_gate import SubscriptionGateMiddleware
from src.services.scheduler import run_periodic


async def main():
    """Основная функция."""
    config: Config = load_config()

    level = getattr(logging, str(config.log.level).upper(), logging.INFO)
    logging.basicConfig(level=level, format=config.log.format)

    bot = Bot(
        token=config.bot.token,
        default=DefaultBotProperties(parse_mode="HTML")
    )
    dp = Dispatcher()

    gate = SubscriptionGateMiddleware()
    dp.message.middleware(gate)
    dp.callback_query.middleware(gate)

    # admin.router уже включает student, queue, subscription, vip
    dp.include_router(admin.router)

    asyncio.create_task(run_periodic(bot))

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
