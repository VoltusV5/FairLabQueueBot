"""Оплата и подписка чата (YooKassa)."""

from __future__ import annotations

import logging
import math
import re
import uuid
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.exc import IntegrityError

from config import Config, load_config
from src.services.subscription import effective_access, has_base_features, current_access_deadline
from src.db.db import get_db
from src.db import queries as Q
from src.handlers import queue_common as qc

logger = logging.getLogger(__name__)


def _parse_yookassa_amount(val) -> float:
    """YooKassa возвращает value строкой; защита от ошибочных форматов вроде '1200.0.00'."""
    s = str(val).strip().replace(",", ".")
    m = re.fullmatch(r"(\d+)\.\d+\.(\d{2})", s)
    if m:
        s = f"{m.group(1)}.{m.group(2)}"
    return float(s)


router = Router()
config: Config = load_config()

try:
    from dotenv import load_dotenv
    from yookassa import Configuration, Payment

    load_dotenv()
    if config.yookassa:
        Configuration.account_id = config.yookassa.shop_id
        Configuration.secret_key = config.yookassa.secret_key
except Exception:
    Payment = None  # type: ignore
    Configuration = None  # type: ignore


@router.message(Command("pay", "sub"))
async def cmd_pay(message: Message) -> None:
    if not config.yookassa or Payment is None:
        await message.answer("Оплата не настроена (YOOKASSA_* в .env).")
        return
    cid = message.chat.id
    
    with get_db() as db:
        chat = Q.ensure_chat(db, cid, message.chat.title)
        acc = effective_access(chat)
        deadline = current_access_deadline(chat)
        
    is_active = has_base_features(acc)
    sub_status = "активна ✅" if is_active else "не активна ❌"
    
    if is_active and deadline:
        tier_label = chat.subscription_tier.upper() if chat else "TRIAL"
        secs = (deadline - datetime.utcnow()).total_seconds()
        if secs <= 0:
            status_text = f"<b>Текущая подписка:</b> {sub_status} ({tier_label})"
        elif secs < 86400:
            status_text = (
                f"<b>Текущая подписка:</b> {sub_status} ({tier_label})\n"
                f"<b>Осталось:</b> менее суток (до {qc.format_dt_msk_compact(deadline)} МСК)"
            )
        else:
            days = max(1, math.ceil(secs / 86400))
            status_text = (
                f"<b>Текущая подписка:</b> {sub_status} ({tier_label})\n"
                f"<b>Осталось:</b> {days} дн. (до {qc.format_dt_msk_compact(deadline)} МСК)"
            )
    else:
        status_text = f"<b>Текущая подписка:</b> {sub_status}"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Base (1 мес) — 149 ₽", callback_data=f"pay|base_1|{cid}"),
                InlineKeyboardButton(text="SuperVIP (1 мес) — 249 ₽", callback_data=f"pay|svip_1|{cid}"),
            ],
            [
                InlineKeyboardButton(text="Base (1 год) — 1490 ₽", callback_data=f"pay|base_12|{cid}"),
                InlineKeyboardButton(text="SuperVIP (1 год) — 1990 ₽", callback_data=f"pay|svip_12|{cid}"),
            ],
            [
                InlineKeyboardButton(text="💎 Что даёт SuperVIP?", callback_data=f"pay_info"),
            ]
        ]
    )
    await message.answer(
        f"<b>Управление подпиской чата</b>\n\n"
        f"{status_text}\n\n"
        "Выберите тарифный план для продления доступа. "
        "Время суммируется с текущим остатком.",
        reply_markup=kb,
    )


@router.callback_query(F.data == "pay_info")
async def cb_pay_info(callback: CallbackQuery) -> None:
    text = (
        "<b>👑 Преимущества SuperVIP:</b>\n\n"
        "• <b>Постоянные группы (/group):</b> Объединяйтесь с друзьями! Бот будет ставить вашу мини-группу единым блоком, чтобы вы могли сдавать лабы вместе. Приоритет группы считается по справедливости для всех её участников.\n\n"
        "• <b>Мгновенная вставка (/insert):</b> Нужно добавить человека прямо сейчас? Вставляйте любого участника на любое место в уже сформированной очереди одним сообщением.\n\n"
        "• <b>Полный контроль завершения (/last):</b> Не ждите таймеров! Отмечайте последнего сдавшего вручную (даже если этот человек сам не нажимает кнопку), и бот мгновенно закроет очередь и рассчитает статистику.\n\n"
        "• <b>Умное перемешивание (/shuffle):</b> Хотите абсолютный рандом? Перемешайте список участников одним кликом, если старый порядок вам не подходит.\n\n"
        "<i>Все функции Base (опросы, статистика, автозакрытие) уже включены!</i>"
    )
    await callback.message.answer(text)
    await callback.answer()


@router.callback_query(F.data == "pay_cancel")
async def cb_pay_cancel(callback: CallbackQuery) -> None:
    await callback.message.edit_text("❌ Оплата отменена. Вы можете вернуться к выбору тарифа через /pay.")
    await callback.answer()


@router.callback_query(F.data.startswith("pay|"))
async def cb_pay(callback: CallbackQuery) -> None:
    if not config.yookassa or Payment is None:
        await callback.answer("Оплата недоступна.", show_alert=True)
        return
    
    # pay|tier_months|chat_id
    _, plan, cid_s = callback.data.split("|")
    chat_id = int(cid_s)
    
    tier_code, months_s = plan.split("_")
    months = int(months_s)
    
    meta_tier = "base" if tier_code == "base" else "supervip"

    prices = {
        "base_1": 149,
        "svip_1": 249,
        "base_12": 1490,
        "svip_12": 1990,
    }
    if plan not in prices:
        await callback.answer("Неизвестный тариф.", show_alert=True)
        return

    amount = f"{prices[plan]}.00"
    desc = (
        f"Base подписка ({months} мес.)"
        if meta_tier == "base"
        else f"SuperVIP подписка ({months} мес.)"
    )

    # Логика доплаты (Upgrade) или Даунгрейда (Downgrade)
    upgrade_mode = False
    with get_db() as db:
        chat = Q.get_chat(db, chat_id)
        if chat:
            deadline = current_access_deadline(chat)
            now = datetime.utcnow()
            remaining_days = (deadline - now).total_seconds() / 86400.0 if deadline and deadline > now else 0

            # Если у пользователя SuperVIP, запрещаем покупать Base
            if chat.subscription_tier == "supervip" and meta_tier == "base":
                if remaining_days > 15:
                    await callback.answer(
                        f"У вас активен SuperVIP. Покупка Base будет доступна, когда останется менее 15 дней (сейчас {int(remaining_days)}).",
                        show_alert=True
                    )
                    return

            # Апгрейд с Base до SuperVIP за оставшийся период: фикс. доплата за день
            if meta_tier == "supervip" and chat.subscription_tier == "base" and remaining_days > 0:
                rub_per_day = 3.0 if remaining_days < 30 else 2.0
                upgrade_cost = remaining_days * rub_per_day
                upgrade_amount_int = max(10, int(upgrade_cost + 0.99))
                amount = f"{upgrade_amount_int}.00"
                desc = (
                    f"Апгрейд Base → SuperVIP на {int(remaining_days)} дн. "
                    f"({rub_per_day:g} ₽/день)"
                )
                upgrade_mode = True
                
                # В этом режиме мы НЕ добавляем новые месяцы, а просто повышаем уровень
                # Поэтому в метаданных передаем 0 месяцев
                months = 0

    await callback.answer("Создаём платёж…")
    idem = str(uuid.uuid4())
    try:
        pay = Payment.create(
            {
                "amount": {"value": amount, "currency": "RUB"},
                "confirmation": {
                    "type": "redirect",
                    "return_url": config.yookassa.return_url,
                },
                "capture": True,
                "description": f"{desc} chat={chat_id}",
                "metadata": {
                    "chat_id": str(chat_id),
                    "tier": meta_tier,
                    "months": str(months),
                    "upgrade": "1" if upgrade_mode else "0"
                },
            },
            idem,
        )
    except Exception as e:
        logger.exception("yookassa create: %s", e)
        await callback.message.answer(f"Ошибка создания платежа: {e}")
        return
    
    builder = InlineKeyboardBuilder()
    builder.button(text="Оплатить", url=pay.confirmation.confirmation_url)
    builder.button(text="Проверить оплату", callback_data=f"chk|{pay.id}")
    builder.button(text="Отменить", callback_data="pay_cancel")
    builder.adjust(1)
    await callback.message.edit_text(
        f"<b>Счёт: {amount} ₽</b>\n{desc}\n\nПосле оплаты нажмите кнопку «Проверить оплату».",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("chk|"))
async def cb_check(callback: CallbackQuery) -> None:
    if Payment is None:
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
        await callback.answer("Некорректная сумма в ответе банка.", show_alert=True)
        return

    with get_db() as db:
        if Q.get_payment_by_yookassa_id(db, pid):
            await callback.message.edit_text("✅ Этот платёж уже был учтён ранее.")
            await callback.answer()
            return

        try:
            if is_upgrade:
                Q.apply_upgrade_tier_only(db, chat_id, tier, commit=False)
            else:
                Q.apply_paid_subscription(
                    db, chat_id, tier, amount_paid=amt, months=months, commit=False
                )
            Q.create_payment_record(
                db, pid, chat_id, tier, str(amt), "succeeded", commit=False
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            with get_db() as db2:
                if Q.get_payment_by_yookassa_id(db2, pid):
                    await callback.message.edit_text(
                        "✅ Этот платёж уже был учтён ранее."
                    )
                else:
                    logger.exception(
                        "payment finalize IntegrityError payment_id=%s", pid
                    )
                    await callback.answer(
                        "Ошибка записи платежа. Попробуйте позже или напишите в поддержку.",
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

