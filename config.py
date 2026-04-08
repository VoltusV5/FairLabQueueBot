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
class YooKassaCfg:
    """ЮKassa (опционально)."""

    shop_id: str
    secret_key: str
    return_url: str


@dataclass
class Config:
    """Класс для настройки ТГ бота."""

    bot: TgBot
    log: LogSettings
    yookassa: YooKassaCfg | None

    def is_super_admin(self, user_id: int) -> bool:
        """ID из SUPER_ADMINS в .env — для будущих админ-команд и аудита."""
        return user_id in self.bot.admin_ids


def load_config(path: str | None = None) -> Config:
    """Непосредственная загрузка данных из .env."""
    env = Env()
    env.read_env(path)
    admin_ids = [int(admin_id) for admin_id in env.str("SUPER_ADMINS").split(",")]
    shop = env.str("YOOKASSA_SHOP_ID", "")
    sec = env.str("YOOKASSA_SECRET_KEY", "")
    ret = env.str("YOOKASSA_RETURN_URL", "https://t.me/FairLabQueueBot")
    yk = (
        YooKassaCfg(shop_id=shop, secret_key=sec, return_url=ret)
        if shop and sec
        else None
    )
    return Config(
        bot=TgBot(token=env("BOT_TOKEN"), admin_ids=admin_ids),
        log=LogSettings(level=env("LOG_LEVEL"), format=env("LOG_FORMAT")),
        yookassa=yk,
    )
