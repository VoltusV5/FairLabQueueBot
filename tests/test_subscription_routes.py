"""Тесты для обработчиков оплаты и подписки (src/handlers/subscription.py)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Chat, Message, User
from sqlalchemy.ext.asyncio import AsyncSession
from src.db import queries as queries_db
from src.handlers import subscription


def _create_message(
    text: str,
    chat_id: int = 100,
    chat_type: ChatType = ChatType.GROUP,
    message_id: int = 1,
    user_id: int = 1001,
) -> Message:
    """Создает мок объекта aiogram Message."""
    msg = MagicMock(spec=Message)
    msg.text = text
    msg.message_id = message_id
    msg.chat = MagicMock(spec=Chat)
    msg.chat.id = chat_id
    msg.chat.type = chat_type
    msg.chat.title = "Тестовый чат"
    msg.from_user = MagicMock(spec=User)
    msg.from_user.id = user_id
    msg.from_user.username = "testuser"
    msg.from_user.first_name = "Test"
    msg.from_user.full_name = "Test User"

    msg.answer = AsyncMock()
    msg.edit_text = AsyncMock()
    msg.edit_reply_markup = AsyncMock()
    return msg


def _create_callback(
    data: str | None,
    chat_id: int = 100,
    message_id: int = 1,
    user_id: int = 1001,
) -> CallbackQuery:
    """Создает мок объекта aiogram CallbackQuery."""
    cb = MagicMock(spec=CallbackQuery)
    cb.data = data
    cb.from_user = MagicMock(spec=User)
    cb.from_user.id = user_id
    cb.from_user.username = "testuser"
    cb.from_user.first_name = "Test"
    cb.from_user.full_name = "Test User"

    msg = MagicMock(spec=Message)
    msg.text = "Текст сообщения"
    msg.message_id = message_id
    msg.chat = MagicMock(spec=Chat)
    msg.chat.id = chat_id
    msg.edit_text = AsyncMock()
    msg.answer = AsyncMock()

    cb.message = msg
    cb.answer = AsyncMock()
    return cb


@pytest.mark.asyncio
async def test_cmd_pay_no_yookassa(
    async_session: AsyncSession,
) -> None:
    """Проверка команды /pay, когда платежи не настроены."""
    msg = _create_message("/pay", chat_id=100)
    mock_cfg = MagicMock()
    mock_cfg.yookassa = None
    with patch("src.handlers.subscription.bot_config", mock_cfg):
        await subscription.cmd_pay(msg, async_session)
    msg.answer.assert_awaited_once()
    assert "Оплата не настроена" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_cmd_pay_active_trial(
    async_session: AsyncSession,
) -> None:
    """Проверка вывода статуса подписки в /pay при активном триале."""
    db = async_session
    chat_id = 200
    chat = await queries_db.ensure_chat(db, chat_id, "Тестовый чат")
    now = datetime.now(UTC)
    chat.trial_ends_at = now + timedelta(days=20)
    await db.flush()

    msg = _create_message("/pay", chat_id=chat_id)
    mock_cfg = MagicMock()
    mock_cfg.yookassa = MagicMock()
    with (
        patch("src.handlers.subscription.bot_config", mock_cfg),
        patch("src.handlers.subscription.Payment", MagicMock()),
    ):
        await subscription.cmd_pay(msg, db)

    msg.answer.assert_awaited_once()
    ans = msg.answer.await_args.args[0]
    assert "активна ✅" in ans
    assert "Управление подпиской чата" in ans
    assert msg.answer.await_args.kwargs.get("reply_markup") is not None


@pytest.mark.asyncio
async def test_cmd_pay_expired(
    async_session: AsyncSession,
) -> None:
    """Проверка вывода статуса подписки в /pay при истёкшей подписке."""
    db = async_session
    chat_id = 250
    chat = await queries_db.ensure_chat(db, chat_id, "Тестовый чат")
    past = datetime.now(UTC) - timedelta(days=5)
    chat.trial_ends_at = past
    chat.subscription_ends_at = past
    await db.flush()

    msg = _create_message("/pay", chat_id=chat_id)
    mock_cfg = MagicMock()
    mock_cfg.yookassa = MagicMock()
    with (
        patch("src.handlers.subscription.bot_config", mock_cfg),
        patch("src.handlers.subscription.Payment", MagicMock()),
    ):
        await subscription.cmd_pay(msg, db)

    msg.answer.assert_awaited_once()
    ans = msg.answer.await_args.args[0]
    assert "не активна ❌" in ans


@pytest.mark.asyncio
async def test_cb_pay_info_and_cancel() -> None:
    """Проверка информационного окна SuperVIP и отмены платежа."""
    cb_info = _create_callback("pay_info")
    await subscription.cb_pay_info(cb_info)
    assert cb_info.message is not None
    cb_info.message.answer.assert_awaited_once()
    assert "Преимущества SuperVIP" in cb_info.message.answer.await_args.args[0]
    cb_info.answer.assert_awaited_once()

    cb_cancel = _create_callback("pay_cancel")
    await subscription.cb_pay_cancel(cb_cancel)
    assert cb_cancel.message is not None
    cb_cancel.message.edit_text.assert_awaited_once()
    assert "Оплата отменена" in cb_cancel.message.edit_text.await_args.args[0]
    cb_cancel.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_cb_pay_create_payment_success(
    async_session: AsyncSession,
) -> None:
    """Проверка успешной генерации счета и кнопок оплаты через YooKassa."""
    db = async_session
    chat_id = 300
    await queries_db.ensure_chat(db, chat_id, "Чат")

    cb = _create_callback(f"pay|base_1|{chat_id}", chat_id=chat_id)
    mock_payment = MagicMock()
    mock_pay_obj = MagicMock()
    mock_pay_obj.id = "yoo_test_123"
    mock_pay_obj.confirmation.confirmation_url = "https://yoomoney.ru/checkout"
    mock_payment.create.return_value = mock_pay_obj

    mock_cfg = MagicMock()
    mock_cfg.yookassa.return_url = "https://t.me/bot"

    with (
        patch("src.handlers.subscription.bot_config", mock_cfg),
        patch("src.handlers.subscription.Payment", mock_payment),
    ):
        await subscription.cb_pay(cb, db)

    assert cb.message is not None
    cb.message.edit_text.assert_awaited_once()
    text = cb.message.edit_text.await_args.args[0]
    assert "Счёт: 149.00 ₽" in text
    mock_payment.create.assert_called_once()


@pytest.mark.asyncio
async def test_cb_pay_upgrade_prompt_for_base_user(
    async_session: AsyncSession,
) -> None:
    """Проверка показа меню апгрейда для пользователя с активным Base."""
    db = async_session
    chat_id = 400
    chat = await queries_db.ensure_chat(db, chat_id, "Чат")
    chat.subscription_tier = "base"
    chat.subscription_ends_at = datetime.now(UTC) + timedelta(days=20)
    await db.flush()

    cb = _create_callback(f"pay|svip_1|{chat_id}", chat_id=chat_id)
    mock_cfg = MagicMock()
    mock_cfg.yookassa = MagicMock()
    with (
        patch("src.handlers.subscription.bot_config", mock_cfg),
        patch("src.handlers.subscription.Payment", MagicMock()),
    ):
        await subscription.cb_pay(cb, db)

    assert cb.message is not None
    cb.message.edit_text.assert_awaited_once()
    text = cb.message.edit_text.await_args.args[0]
    assert "Выберите вариант перехода на SuperVIP" in text


@pytest.mark.asyncio
async def test_cb_check_payment_success(
    async_session: AsyncSession,
) -> None:
    """Проверка подтверждения успешной оплаты и активации тарифа."""
    db = async_session
    chat_id = 500
    await queries_db.ensure_chat(db, chat_id, "Чат")

    cb = _create_callback("chk|pay_succ_999", chat_id=chat_id)

    mock_pay = MagicMock()
    mock_pay.status = "succeeded"
    mock_pay.metadata = {
        "chat_id": str(chat_id),
        "tier": "supervip",
        "months": "1",
        "upgrade": "0",
    }
    mock_pay.amount.value = "249.00"

    mock_payment_cls = MagicMock()
    mock_payment_cls.find_one.return_value = mock_pay

    with patch("src.handlers.subscription.Payment", mock_payment_cls):
        await subscription.cb_check(cb, db)

    assert cb.message is not None
    cb.message.edit_text.assert_awaited_once()
    text = cb.message.edit_text.await_args.args[0]
    assert "Оплата получена" in text
    assert "SUPERVIP" in text

    # Проверяем, что в БД записался платеж и обновился чат
    payment_rec = await queries_db.get_payment_by_yookassa_id(
        db, "pay_succ_999"
    )
    assert payment_rec is not None
    assert payment_rec.status == "succeeded"
    assert payment_rec.tier == "supervip"

    updated_chat = await queries_db.get_chat(db, chat_id)
    assert updated_chat is not None
    assert updated_chat.subscription_tier == "supervip"


@pytest.mark.asyncio
async def test_cb_check_payment_already_recorded(
    async_session: AsyncSession,
) -> None:
    """Проверка защиты от повторной обработки уже зачтенного платежа."""
    db = async_session
    chat_id = 600
    await queries_db.ensure_chat(db, chat_id, "Чат")
    await queries_db.create_payment_record(
        db,
        "pay_dup_111",
        chat_id,
        "base",
        "149.00",
        "succeeded",
        flush=True,
    )

    cb = _create_callback("chk|pay_dup_111", chat_id=chat_id)

    mock_pay = MagicMock()
    mock_pay.status = "succeeded"
    mock_pay.metadata = {"chat_id": str(chat_id), "tier": "base"}
    mock_pay.amount.value = "149.00"

    mock_payment_cls = MagicMock()
    mock_payment_cls.find_one.return_value = mock_pay

    with patch("src.handlers.subscription.Payment", mock_payment_cls):
        await subscription.cb_check(cb, db)

    assert cb.message is not None
    cb.message.edit_text.assert_awaited_once()
    text = cb.message.edit_text.await_args.args[0]
    assert "уже был учтён ранее" in text


@pytest.mark.asyncio
async def test_cb_check_payment_pending(
    async_session: AsyncSession,
) -> None:
    """Проверка уведомления, если платеж еще не оплачен в банке."""
    cb = _create_callback("chk|pay_pending_222")

    mock_pay = MagicMock()
    mock_pay.status = "pending"
    mock_payment_cls = MagicMock()
    mock_payment_cls.find_one.return_value = mock_pay

    with patch("src.handlers.subscription.Payment", mock_payment_cls):
        await subscription.cb_check(cb, async_session)

    cb.answer.assert_awaited_once()
    assert "pending" in cb.answer.await_args.args[0]
