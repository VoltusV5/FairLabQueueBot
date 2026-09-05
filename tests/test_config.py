"""Тесты для модуля конфигурации (config.py)."""

from __future__ import annotations

import dataclasses

import pytest
from config import Config, LogSettings, TgBot, YooKassaCfg, load_config


def test_load_config_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверка успешной загрузки полной конфигурации."""
    monkeypatch.setenv("BOT_TOKEN", "123456:ABC-DEF1234ghIkl-zyx57W2v")
    monkeypatch.setenv("SUPER_ADMINS", "1001, 1002 , 1003")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("LOG_FORMAT", "%(message)s")
    monkeypatch.setenv("YOOKASSA_SHOP_ID", "test_shop")
    monkeypatch.setenv("YOOKASSA_SECRET_KEY", "test_key")
    monkeypatch.setenv("YOOKASSA_RETURN_URL", "https://t.me/test_bot")

    cfg = load_config()

    assert cfg.bot.token == "123456:ABC-DEF1234ghIkl-zyx57W2v"
    assert cfg.bot.admin_ids == [1001, 1002, 1003]
    assert cfg.log.level == "DEBUG"
    assert cfg.log.format == "%(message)s"
    assert cfg.yookassa is not None
    assert cfg.yookassa.shop_id == "test_shop"
    assert cfg.yookassa.secret_key == "test_key"
    assert cfg.yookassa.return_url == "https://t.me/test_bot"


def test_load_config_missing_bot_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверка вызова исключения при отсутствии BOT_TOKEN."""
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.setenv("SUPER_ADMINS", "1001")

    with pytest.raises(ValueError, match="BOT_TOKEN"):
        load_config()


def test_load_config_resilient_admins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверка устойчивости парсинга SUPER_ADMINS к пробелам и мусору."""
    monkeypatch.setenv("BOT_TOKEN", "fake_token")
    monkeypatch.setenv("SUPER_ADMINS", "  1001, invalid, 1002 , , 1003 ")

    cfg = load_config()
    assert cfg.bot.admin_ids == [1001, 1002, 1003]

    monkeypatch.setenv("SUPER_ADMINS", "")
    cfg_empty = load_config()
    assert cfg_empty.bot.admin_ids == []


def test_load_config_without_yookassa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверка загрузки без ЮKassa (опциональная интеграция)."""
    monkeypatch.setenv("BOT_TOKEN", "fake_token")
    monkeypatch.delenv("YOOKASSA_SHOP_ID", raising=False)
    monkeypatch.delenv("YOOKASSA_SECRET_KEY", raising=False)

    cfg = load_config()
    assert cfg.yookassa is None


def test_is_super_admin() -> None:
    """Проверка метода is_super_admin."""
    cfg = Config(
        bot=TgBot(token="tok", admin_ids=[111, 222]),
        log=LogSettings(level="INFO", format="%(message)s"),
        yookassa=None,
    )
    assert cfg.is_super_admin(111) is True
    assert cfg.is_super_admin(222) is True
    assert cfg.is_super_admin(333) is False


def test_dataclasses_immutability() -> None:
    """Проверка неизменяемости (frozen=True) структур данных конфигурации."""
    bot = TgBot(token="token", admin_ids=[1])
    with pytest.raises(dataclasses.FrozenInstanceError):
        bot.token = "new_token"  # type: ignore[misc]

    cfg = Config(
        bot=bot,
        log=LogSettings(level="INFO", format="fmt"),
        yookassa=None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.yookassa = YooKassaCfg("a", "b", "c")  # type: ignore[misc]
