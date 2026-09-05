"""Загрузка и валидация конфигурации приложения из переменных окружения.

Модуль считывает параметры из файла .env и переменных окружения,
предоставляя типизированные неизменяемые структуры данных для бота,
настроек логирования и интеграции с ЮKassa.
"""

from __future__ import annotations

from dataclasses import dataclass

from environs import Env

__all__ = [
    "Config",
    "LogSettings",
    "TgBot",
    "YooKassaCfg",
    "load_config",
]


@dataclass(slots=True, frozen=True)
class TgBot:
    """Параметры подключения и администрирования Telegram-бота.

    Attributes:
        token: Секретный токен бота, выданный BotFather.
        admin_ids: Список Telegram ID суперадминистраторов бота.
    """

    token: str
    admin_ids: list[int]


@dataclass(slots=True, frozen=True)
class LogSettings:
    """Параметры подсистемы логирования приложения.

    Attributes:
        level: Уровень логирования (DEBUG, INFO, WARNING, ERROR).
        format: Шаблон форматирования записей журнала.
    """

    level: str
    format: str


@dataclass(slots=True, frozen=True)
class YooKassaCfg:
    """Параметры интеграции с платежным сервисом ЮKassa.

    Attributes:
        shop_id: Идентификатор магазина в ЮKassa.
        secret_key: Секретный ключ API для работы с платежами.
        return_url: URL перенаправления пользователя после оплаты.
    """

    shop_id: str
    secret_key: str
    return_url: str


@dataclass(slots=True, frozen=True)
class Config:
    """Главная структура настроек конфигурации приложения.

    Attributes:
        bot: Настройки Telegram-бота.
        log: Настройки логирования.
        yookassa: Настройки ЮKassa либо None, если платежи не настроены.
    """

    bot: TgBot
    log: LogSettings
    yookassa: YooKassaCfg | None

    def is_super_admin(self, user_id: int) -> bool:
        """Проверить, является ли пользователь суперадминистратором бота.

        Args:
            user_id: Telegram ID пользователя.

        Returns:
            True, если user_id входит в список admin_ids, иначе False.
        """
        return user_id in self.bot.admin_ids


def load_config(path: str | None = None) -> Config:
    """Загрузить конфигурацию из файла .env и переменных окружения.

    Args:
        path: Путь к файлу .env или None для стандартного поиска.

    Returns:
        Заполненный объект Config.

    Raises:
        ValueError: Если отсутствует обязательный токен BOT_TOKEN.
    """
    env = Env()
    env.read_env(path)

    raw_admins = env.str("SUPER_ADMINS", "").strip()
    admin_ids: list[int] = []
    if raw_admins:
        for part in raw_admins.split(","):
            part_clean = part.strip()
            if part_clean.isdigit():
                admin_ids.append(int(part_clean))

    shop = env.str("YOOKASSA_SHOP_ID", "").strip()
    sec = env.str("YOOKASSA_SECRET_KEY", "").strip()
    ret = env.str(
        "YOOKASSA_RETURN_URL", "https://t.me/FairLabQueueBot"
    ).strip()

    yk = (
        YooKassaCfg(shop_id=shop, secret_key=sec, return_url=ret)
        if shop and sec
        else None
    )

    token = env.str("BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("Переменная окружения BOT_TOKEN не задана или пуста.")

    log_level = env.str("LOG_LEVEL", "INFO").strip()
    log_format = env.str(
        "LOG_FORMAT",
        "%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    ).strip()

    return Config(
        bot=TgBot(token=token, admin_ids=admin_ids),
        log=LogSettings(level=log_level, format=log_format),
        yookassa=yk,
    )
