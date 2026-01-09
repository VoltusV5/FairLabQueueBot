"""Главный файл с инициализацией бота."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from config import Config, load_config
from src.handlers import student, admin
from src.services import queue_manager


async def main():
    """Основная функция."""
    # Загружаем конфиг в переменную config
    config: Config = load_config()

    # Задаём базовую конфигурацию логирования
    logging.basicConfig(
        level=logging.getLevelName(level=config.log.level),
        format=config.log.format,
    )

    # Инициализируем бот и диспетчер
    bot = Bot(token=config.bot.token)
    dp = Dispatcher()

    # Регистриуем роутеры в диспетчере
    dp.include_router(student.router)
    dp.include_router(admin.router)
    dp.include_router(queue_manager.router)

    # Пропускаем накопившиеся апдейты и запускаем polling
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


asyncio.run(main())
