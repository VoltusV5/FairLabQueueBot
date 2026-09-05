"""Обработчики оплаты и продления подписки чата через сервис YooKassa."""

from __future__ import annotations

import logging
import math
import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import Config, load_config
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from src.db import queries as queries_db
from src.db.repositories.subscription_rate import get_subscription_rates
from src.handlers import queue_common as qc
from src.services.billing_constants import SubscriptionTier
from src.services.subscription import (
    current_access_deadline,
    effective_access,
    has_base_features,
)

logger = logging.getLogger(__name__)

router = Router()

try:
    from dotenv import load_dotenv
    from yookassa import Configuration, Payment

    load_dotenv()
    bot_config: Config | None = load_config()
    if bot_config and bot_config.yookassa and Configuration is not None:
        Configuration.account_id = bot_config.yookassa.shop_id
        Configuration.secret_key = bot_config.yookassa.secret_key
except Exception:
    bot_config = None
    Configuration = None  # type: ignore[assignment]
    Payment = None  # type: ignore[assignment]


def _parse_yookassa_amount(val: Any) -> float:
    """Парсит значение суммы платежа от YooKassa в тип float.

    Защищает от некорректных форматов строки вида '1200.0.00'.

    Args:
        val: Значение суммы из объекта платежа YooKassa.

    Returns:
        float: Числовое представление суммы в рублях.
    """
    s = str(val).strip().replace(",", ".")
    m = re.fullmatch(r"(\d+)\.\d+\.(\d{2})", s)
    if m:
        s = f"{m.group(1)}.{m.group(2)}"
    return float(s)


@router.message(Command("pay", "sub"))
async def cmd_pay(message: Message, session: AsyncSession) -> None:
    """Выводит информацию о текущей подписке чата и меню тарифов оплаты.

    Args:
        message: Входящее сообщение aiogram.
        session: Асинхронная сессия БД.
    """
    if not bot_config or not bot_config.yookassa or Payment is None:
        await message.answer("Оплата не настроена (YOOKASSA_* в .env).")
        return

    cid = message.chat.id
    db = session
    chat = await queries_db.ensure_chat(db, cid, message.chat.title)
    acc = effective_access(chat)
    deadline = current_access_deadline(chat)

    is_active = has_base_features(acc)
    sub_status = "активна ✅" if is_active else "не активна ❌"

    if is_active and deadline:
        tier_label = chat.subscription_tier.upper() if chat else "TRIAL"
        if deadline.tzinfo is None:
            deadline_aware = deadline.replace(tzinfo=UTC)
        else:
            deadline_aware = deadline
        now = datetime.now(UTC)
        secs = (deadline_aware - now).total_seconds()

        dt_formatted = qc.format_dt_msk_compact(deadline)
        if secs <= 0:
            status_text = (
                f"<b>Текущая подписка:</b> {sub_status} ({tier_label})"
            )
        elif secs < 86400:
            status_text = (
                "<b>Текущая подписка:</b> "
                f"{sub_status} ({tier_label})\n"
                "<b>Осталось:</b> "
                f"менее суток (до {dt_formatted} МСК)"
            )
        else:
            days = max(1, math.ceil(secs / 86400))
            status_text = (
                "<b>Текущая подписка:</b> "
                f"{sub_status} ({tier_label})\n"
                "<b>Осталось:</b> "
                f"{days} дн. (до {dt_formatted} МСК)"
            )
    else:
        status_text = "<b>Текущая подписка:</b> " + str(sub_status)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Base (1 мес) — 149 ₽",
                    callback_data=f"pay|base_1|{cid}",
                ),
                InlineKeyboardButton(
                    text="SuperVIP (1 мес) — 249 ₽",
                    callback_data=f"pay|svip_1|{cid}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Base (1 год) — 1490 ₽",
                    callback_data=f"pay|base_12|{cid}",
                ),
                InlineKeyboardButton(
                    text="SuperVIP (1 год) — 1990 ₽",
                    callback_data=f"pay|svip_12|{cid}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💎 Что даёт SuperVIP?",
                    callback_data="pay_info",
                ),
            ],
        ]
    )
    await message.answer(
        f"<b>Управление подпиской чата</b>\n\n"
        f"{status_text}\n\n"
        "Выберите тарифный план для продления доступа. "
        "Время суммируется с текущим остатком. "
        "(СБП, банковская карта, ЮMoney, T-Pay, SberPay)",
        reply_markup=kb,
    )


@router.callback_query(F.data == "pay_info")
async def cb_pay_info(callback: CallbackQuery) -> None:
    """Отправляет описание расширенных возможностей подписки SuperVIP.

    Args:
        callback: Входящий callback-запрос aiogram.
    """
    if not isinstance(callback.message, Message):
        await callback.answer()
        return

    text = (
        "<b>👑 Преимущества SuperVIP:</b>\n\n"
        "• <b>Постоянные группы (/group):</b> Объединяйтесь с друзьями! "
        "Бот будет ставить вашу группу единым блоком, чтобы вы могли сдавать "
        "лабы вместе. Приоритет группы считается по справедливости.\n\n"
        "• <b>Мгновенная вставка (/insert):</b> Нужно добавить человека? "
        "Вставляйте участника на любое место в уже сформированной очереди.\n\n"
        "• <b>Полный контроль завершения (/last):</b> Не ждите таймеров! "
        "Отмечайте последнего сдавшего вручную в любой момент.\n\n"
        "• <b>Умное перемешивание (/shuffle):</b> Случайный порядок "
        "участников очереди в один клик.\n\n"
        "<i>Все функции Base (опросы, статистика, автозакрытие) включены!</i>"
    )
    await callback.message.answer(text)
    await callback.answer()


@router.callback_query(F.data == "pay_cancel")
async def cb_pay_cancel(callback: CallbackQuery) -> None:
    """Обрабатывает отмену выбора тарифа или генерации счета.

    Args:
        callback: Входящий callback-запрос aiogram.
    """
    if not isinstance(callback.message, Message):
        await callback.answer()
        return

    await callback.message.edit_text(
        "❌ Оплата отменена. Вы можете вернуться к выбору тарифа через /pay."
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pay|"))
async def cb_pay(callback: CallbackQuery, session: AsyncSession) -> None:
    """Формирует счет в YooKassa и отправляет ссылку для оплаты.

    Args:
        callback: Входящий callback-запрос aiogram.
        session: Асинхронная сессия БД.
    """
    if not bot_config or not bot_config.yookassa or Payment is None:
        await callback.answer("Оплата недоступна.", show_alert=True)
        return

    if not callback.data or not isinstance(callback.message, Message):
        await callback.answer()
        return

    parts = callback.data.split("|")
    if len(parts) != 3:
        return
    _, plan, cid_s = parts
    chat_id = int(cid_s)

    tier_code, months_s = plan.split("_")
    months = int(months_s)

    if tier_code in ("base", "svip", "svupg", "svfull"):
        meta_tier = "base" if tier_code == "base" else "supervip"
    else:
        await callback.answer("Неизвестный тариф.", show_alert=True)
        return

    prices = {
        "base_1": 149,
        "svip_1": 249,
        "base_12": 1490,
        "svip_12": 1990,
    }
    if tier_code in ("base", "svip") and plan not in prices:
        await callback.answer("Неизвестный тариф.", show_alert=True)
        return

    amount = f"{prices.get(plan, 0)}.00"
    desc = (
        f"Base подписка ({months} мес.)"
        if meta_tier == "base"
        else f"SuperVIP подписка ({months} мес.)"
    )
    if tier_code == "svfull":
        full_plan = f"svip_{months}"
        if full_plan not in prices:
            await callback.answer("Неизвестный тариф.", show_alert=True)
            return
        amount = f"{prices[full_plan]}.00"
        desc = f"SuperVIP подписка ({months} мес.)"
    if tier_code == "svupg":
        amount = "0.00"
        desc = "Апгрейд Base → SuperVIP"

    upgrade_mode = False
    db = session
    chat = await queries_db.get_chat(db, chat_id)
    if chat:
        deadline = current_access_deadline(chat)
        now = datetime.now(UTC)
        if deadline and deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        remaining_days = (
            (deadline - now).total_seconds() / 86400.0
            if deadline and deadline > now
            else 0.0
        )

        if chat.subscription_tier == "supervip" and meta_tier == "base":
            if remaining_days > 15:
                rem_int = int(remaining_days)
                await callback.answer(
                    "У вас активен SuperVIP. Покупка Base будет доступна, "
                    f"когда останется менее 15 дней (сейчас {rem_int}).",
                    show_alert=True,
                )
                return

        if (
            tier_code == "svip"
            and chat.subscription_tier == "base"
            and remaining_days > 0
        ):
            rub_per_day = 3.0 if remaining_days < 30 else 2.0
            upgrade_cost = remaining_days * rub_per_day
            upgrade_amount_int = max(10, int(upgrade_cost + 0.99))
            full_price = prices.get(plan, 0)
            choose = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=(
                                "⚡ Апгрейд до конца срока — "
                                f"{upgrade_amount_int} ₽"
                            ),
                            callback_data=f"pay|svupg_{months}|{chat_id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text=(
                                f"🗓 Полный SuperVIP ({months} мес.) — "
                                f"{full_price} ₽"
                            ),
                            callback_data=f"pay|svfull_{months}|{chat_id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="Отменить",
                            callback_data="pay_cancel",
                        )
                    ],
                ]
            )
            rem_days_int = int(remaining_days)
            rub_str = format(rub_per_day, "g")
            await callback.message.edit_text(
                "<b>Выберите вариант перехода на SuperVIP</b>\n\n"
                "Текущий Base-остаток: "
                f"~{rem_days_int} дн.\n"
                f"Апгрейд учитывает остаток как доплату ({rub_str} ₽/день).\n"
                "Полный период начисляет новый срок SuperVIP по плану.",
                reply_markup=choose,
            )
            await callback.answer()
            return

        if (
            tier_code == "svupg"
            and chat.subscription_tier == "base"
            and remaining_days > 0
        ):
            rates = await get_subscription_rates(db)
            base_rate = Decimal(str(rates.get(SubscriptionTier.BASE, 149)))
            svip_rate = Decimal(str(rates.get(SubscriptionTier.SUPERVIP, 249)))

            diff_per_month = svip_rate - base_rate
            rub_per_day = float(diff_per_month) / 30.0
            upgrade_cost = remaining_days * rub_per_day
            upgrade_amount_int = max(10, int(upgrade_cost + 0.99))
            amount = f"{upgrade_amount_int}.00"
            rem_d = int(remaining_days)
            rate_day_str = format(rub_per_day, ".2f")
            desc = (
                f"Апгрейд Base → SuperVIP на {rem_d} дн. "
                f"({rate_day_str} ₽/день)"
            )
            upgrade_mode = True
            months = 0
        elif tier_code == "svupg":
            await callback.answer(
                "Апгрейд недоступен: нет активного остатка Base.",
                show_alert=True,
            )
            return

        if tier_code == "svfull":
            full_plan = f"svip_{months}"
            if full_plan not in prices:
                await callback.answer("Неизвестный тариф.", show_alert=True)
                return
            amount = f"{prices[full_plan]}.00"
            desc = f"SuperVIP подписка ({months} мес.)"
            upgrade_mode = False

    if tier_code == "svupg" and not upgrade_mode:
        await callback.answer(
            "Апгрейд недоступен: нужен активный Base с остатком срока.",
            show_alert=True,
        )
        return

    await callback.answer("Создаём платёж…")
    idem = str(uuid.uuid4())
    try:
        ret_url = (
            bot_config.yookassa.return_url
            if bot_config.yookassa
            else "https://t.me"
        )
        pay = Payment.create(
            {
                "amount": {"value": amount, "currency": "RUB"},
                "confirmation": {
                    "type": "redirect",
                    "return_url": ret_url,
                },
                "capture": True,
                "description": f"{desc} chat={chat_id}",
                "metadata": {
                    "chat_id": str(chat_id),
                    "tier": meta_tier,
                    "months": str(months),
                    "upgrade": "1" if upgrade_mode else "0",
                },
            },
            idem,
        )
    except Exception as e:
        logger.exception("yookassa create: %s", e)
        await callback.message.answer(f"Ошибка создания платежа: {e}")
        return

    builder = InlineKeyboardBuilder()
    builder.button(
        text="Оплатить (СБП, БК, T-Pay, SberPay)",
        url=pay.confirmation.confirmation_url,
    )
    builder.button(text="Проверить оплату", callback_data=f"chk|{pay.id}")
    builder.button(text="Отменить", callback_data="pay_cancel")
    builder.adjust(1)
    await callback.message.edit_text(
        f"<b>Счёт: {amount} ₽</b>\n{desc}\n\n"
        "После оплаты нажмите кнопку «Проверить оплату».",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("chk|"))
async def cb_check(callback: CallbackQuery, session: AsyncSession) -> None:
    """Проверяет статус оплаты в YooKassa и применяет подписку к чату.

    Args:
        callback: Входящий callback-запрос aiogram.
        session: Асинхронная сессия БД.
    """
    if Payment is None or not callback.data:
        return
    if not isinstance(callback.message, Message):
        await callback.answer()
        return

    pid = callback.data.split("|", 1)[1]
    pay = Payment.find_one(pid)
    if pay.status != "succeeded":
        await callback.answer(f"Статус: {pay.status}", show_alert=True)
        return

    meta = pay.metadata or {}
    chat_id = int(meta.get("chat_id", "0"))
    tier = meta.get("tier", "base")
    is_upgrade = meta.get("upgrade") == "1"
    months = int(meta.get("months") or "0")
    amt_val = getattr(pay.amount, "value", "0")

    try:
        amt = _parse_yookassa_amount(amt_val)
    except ValueError:
        logger.exception("bad amount from yookassa: %r", amt_val)
        await callback.answer(
            "Некорректная сумма в ответе банка.", show_alert=True
        )
        return

    db = session
    if await queries_db.get_payment_by_yookassa_id(db, pid):
        await callback.message.edit_text("✅ Этот платёж уже был учтён ранее.")
        await callback.answer()
        return

    try:
        await queries_db.apply_paid_subscription(
            db, chat_id, tier, amount_paid=amt, months=months, commit=False
        )
        await queries_db.create_payment_record(
            db, pid, chat_id, tier, str(amt), "succeeded", flush=False
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if await queries_db.get_payment_by_yookassa_id(db, pid):
            await callback.message.edit_text(
                "✅ Этот платёж уже был учтён ранее."
            )
        else:
            logger.exception("payment finalize IntegrityError pid=%s", pid)
            await callback.answer(
                "Ошибка записи платежа. Попробуйте позже или напишите нам.",
                show_alert=True,
            )
        await callback.answer()
        return

    res_text = "✅ Оплата получена! "
    if is_upgrade:
        res_text += f"Ваша подписка успешно повышена до {tier.upper()}."
    else:
        res_text += f"Подписка {tier.upper()} активирована/продлена."

    await callback.message.edit_text(res_text)
