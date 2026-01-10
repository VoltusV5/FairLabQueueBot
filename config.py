"""Файл для чтения .env"""

from dataclasses import dataclass
from environs import Env


@dataclass
class TgBot:
    """Класс для настройки ТГ бота."""

    token: str
    admin_ids: list[int]


@dataclass
class LogSettings:
    """Класс для настройки логов."""

    level: str
    format: str


@dataclass
class Config:
    """Класс для настройки ТГ бота."""

    bot: TgBot
    log: LogSettings


def load_config(path: str | None = None) -> Config:
    """Непосредственная загрузка данных из .env."""
    env = Env()
    env.read_env(path)
    admin_ids = [int(admin_id)
                 for admin_id in env.str("SUPER_ADMINS").split(",")]
    return Config(
        bot=TgBot(token=env("BOT_TOKEN"), admin_ids=admin_ids),
        log=LogSettings(level=env("LOG_LEVEL"), format=env("LOG_FORMAT"))
    )
