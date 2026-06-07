"""Работа с БД: пользователи (tg_id), чаты, очереди."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

from sqlalchemy import delete, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .db import get_db
from .init_db import (
    Chat,
    PaymentRecord,
    PresencePoll,
    Queue,
    Subject,
    SubmissionAttempt,
    SwapRequest,
    User,
)

logger = logging.getLogger(__name__)

TRIAL_DAYS = 30
# Промо: для новых чатов до указанного момента выдаём 1 месяц SuperVIP вместо trial.
PROMO_SUPERVIP_UNTIL_UTC = datetime(2026, 4, 19, 23, 59, 59)


def ensure_user(
    db: Session, tg_id: int, tg_username: str | None, real_name: str | None
) -> User:
    u = db.query(User).filter(User.tg_id == tg_id).first()
    if u is None:
        u = User(tg_id=tg_id, tg_username=tg_username, real_name=real_name)
        db.add(u)
        db.commit()
        db.refresh(u)
        return u
    changed = False
    if tg_username is not None and u.tg_username != tg_username:
        u.tg_username = tg_username
        changed = True
    if real_name is not None and u.real_name != real_name:
        u.real_name = real_name
        changed = True
    if changed:
        db.commit()
        db.refresh(u)
    return u


def ensure_chat(db: Session, chat_id: int, title: str | None = None) -> Chat:
    c = db.query(Chat).filter(Chat.chat_id == chat_id).first()
    if c is None:
        now = datetime.utcnow()
        if now <= PROMO_SUPERVIP_UNTIL_UTC:
            c = Chat(
                chat_id=chat_id,
                title=title,
                subscription_tier="supervip",
                trial_ends_at=None,
                subscription_ends_at=now + relativedelta(months=1),
                autoclose_enabled=False,
            )
        else:
            c = Chat(
                chat_id=chat_id,
                title=title,
                subscription_tier="trial",
                trial_ends_at=now + timedelta(days=TRIAL_DAYS),
                subscription_ends_at=None,
                autoclose_enabled=False,
            )
        db.add(c)
        db.commit()
        db.refresh(c)
        return c
    if title and c.title != title:
        c.title = title
        db.commit()
        db.refresh(c)
    return c


def get_chat(db: Session, chat_id: int) -> Chat | None:
    return db.query(Chat).filter(Chat.chat_id == chat_id).first()


def list_all_chats(db: Session):
    return db.query(Chat).all()


def list_subject_names_for_chat(db: Session, chat_id: int) -> list[str]:
    rows = (
        db.query(Subject.subject_name)
        .filter(Subject.chat_id == chat_id)
        .order_by(func.lower(Subject.subject_name))
        .all()
    )
    return [str(r[0]) for r in rows]


def set_chat_autoclose_rules(db: Session, chat_id: int, rules: list | None) -> None:
    c = ensure_chat(db, chat_id)
    c.autoclose_rules = rules
    db.commit()


def get_or_create_subject(db: Session, chat_id: int, subject_name: str) -> Subject:
    s = (
        db.query(Subject)
        .filter(
            Subject.chat_id == chat_id,
            Subject.subject_name == subject_name,
        )
        .first()
    )
    if s:
        return s
    s = Subject(chat_id=chat_id, subject_name=subject_name)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def get_subject_by_id(db: Session, subject_id: int) -> Subject | None:
    return db.query(Subject).filter(Subject.id == subject_id).first()


def get_subject_by_chat_name(
    db: Session, chat_id: int, subject_name: str
) -> Subject | None:
    return (
        db.query(Subject)
        .filter(
            Subject.chat_id == chat_id,
            Subject.subject_name == subject_name,
        )
        .first()
    )


def is_queue_duplicate(
    db: Session, subject_id: int, chat_id: int, lesson_date: datetime
) -> bool:
    q = (
        db.query(Queue)
        .filter(
            Queue.subject_id == subject_id,
            Queue.chat_id == chat_id,
            Queue.lesson_date == lesson_date,
        )
        .first()
    )
    return q is not None


def add_queue_row(
    db: Session,
    *,
    subject_id: int,
    chat_id: int,
    message_id: int,
    lesson_date: datetime,
    close_at: datetime | None,
    status: str,
    participants: list,
    extra: dict | None = None,
) -> Queue:
    row = Queue(
        subject_id=subject_id,
        chat_id=chat_id,
        message_id=message_id,
        lesson_date=lesson_date,
        close_at=close_at,
        status=status,
        participants=list(participants),
        extra=extra or {},
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_queue_by_message_id(db: Session, message_id: int) -> Queue | None:
    return db.query(Queue).filter(Queue.message_id == message_id).first()


def get_queue_by_chat_message(
    db: Session, chat_id: int, message_id: int
) -> Queue | None:
    return (
        db.query(Queue)
        .filter(Queue.chat_id == chat_id, Queue.message_id == message_id)
        .first()
    )


def get_queue_by_subject_lesson(
    db: Session, chat_id: int, subject_id: int, lesson_date: datetime
) -> Queue | None:
    return (
        db.query(Queue)
        .filter(
            Queue.chat_id == chat_id,
            Queue.subject_id == subject_id,
            Queue.lesson_date == lesson_date,
        )
        .first()
    )


def apply_slot_penalties_after_last_submitter(
    db: Session, order: list[int | str], successful_slot_index: int, subject_id: int
) -> None:
    """
    Логика штрафов:
    1. Участники со слотами <= successful_slot_index считаются 'успешными'.
    2. Слоты > successful_slot_index считаются 'нереализованными'.
    3. За каждый нереализованный слот удаляем запись из history_position.
    4. +1 к missed_attempts_count только если у пользователя НЕТ успешных слотов в этой очереди.
    """
    # 1. Кто успел подойти (хотя бы один слот до или на позиции последнего сдавшего)
    successful_uids = set()
    for i in range(successful_slot_index + 1):
        entry = order[i]
        if isinstance(entry, int):
            successful_uids.add(entry)

    # 2. Считаем нереализованные слоты (те, что после последнего сдавшего)
    unrealized_counts: dict[int, int] = {}
    for i in range(successful_slot_index + 1, len(order)):
        entry = order[i]
        if isinstance(entry, int):
            unrealized_counts[entry] = unrealized_counts.get(entry, 0) + 1

    # 3. Применяем изменения
    for uid, count in unrealized_counts.items():
        row = (
            db.query(SubmissionAttempt)
            .filter(
                SubmissionAttempt.tg_id == uid,
                SubmissionAttempt.subject_id == subject_id,
            )
            .first()
        )
        if not row:
            continue

        # Помечаем записи из истории как нереализованные (добавляем "M")
        hp = list(row.history_position or [])
        marked = 0
        for i in range(len(hp) - 1, -1, -1):
            if marked >= count:
                break
            if not str(hp[i]).endswith("M"):
                hp[i] = str(hp[i]) + "M"
                marked += 1
        row.history_position = hp
        flag_modified(row, "history_position")

        # Штраф к missed_attempts только если человек ВООБЩЕ не успел подойти сегодня
        if uid not in successful_uids:
            row.missed_attempts_count = int(row.missed_attempts_count or 0) + count

    db.commit()


def complete_queue_last_submitter(db: Session, q: Queue, tg_id: int) -> None:
    """Завершить очередь: последний сдавший tg_id, штрафы по всем прочим слотам."""
    ex = q.extra or {}
    order = list(ex.get("formed_order") or q.participants or [])
    # Ищем только среди числовых id (игнорируем временные имена)
    indices = [i for i, x in enumerate(order) if isinstance(x, int) and x == tg_id]
    if not indices:
        raise ValueError("Пользователь не в очереди.")
    idx = indices[-1]
    apply_slot_penalties_after_last_submitter(db, order, idx, q.subject_id)
    q.status = "completed"
    db.commit()


def insert_into_formed_queue(
    db: Session, q: Queue, tg_id: int, pos_1based: int
) -> None:
    """Вставить в сформированную очередь (1 — первый)."""
    if q.status != "waiting_for_last_participant":
        raise ValueError("Неверный статус очереди (нужна сформированная).")
    ex = dict(q.extra or {})
    order = list(ex.get("formed_order") or q.participants or [])
    if tg_id in order:
        raise ValueError("Уже в списке.")
    n = len(order)
    if pos_1based < 1 or pos_1based > n + 1:
        raise ValueError("Неверный номер места.")
    order.insert(pos_1based - 1, tg_id)
    q.participants = order
    flag_modified(q, "participants")
    merge_extra(db, q, {"formed_order": order})
    append_one_history_position(db, tg_id, q.subject_id, str(pos_1based))


def get_presence_poll(
    db: Session, chat_id: int, message_id: int
) -> PresencePoll | None:
    return (
        db.query(PresencePoll)
        .filter(
            PresencePoll.chat_id == chat_id,
            PresencePoll.message_id == message_id,
        )
        .first()
    )


def upsert_presence_here(
    db: Session, chat_id: int, message_id: int, tg_id: int, add: bool
) -> bool:
    """Вернуть True если изменилось. add=True — на паре, False — убрать."""
    row = get_presence_poll(db, chat_id, message_id)
    here = list(row.here_tg_ids if row else [])
    if add:
        if tg_id in here:
            return False
        here.append(tg_id)
    else:
        if tg_id not in here:
            return False
        here.remove(tg_id)
    if row is None:
        row = PresencePoll(chat_id=chat_id, message_id=message_id, here_tg_ids=here)
        db.add(row)
    else:
        row.here_tg_ids = here
        flag_modified(row, "here_tg_ids")
    db.commit()
    return True


def update_queue_message_id(db: Session, q: Queue, new_message_id: int) -> None:
    q.message_id = new_message_id
    db.commit()


def set_queue_status(db: Session, q: Queue, status: str) -> None:
    q.status = status
    db.commit()


def merge_extra(db: Session, q: Queue, patch: dict) -> None:
    ex = dict(q.extra or {})
    ex.update(patch)
    q.extra = ex
    flag_modified(q, "extra")
    db.commit()


def add_participant(db: Session, q: Queue, tg_id: int) -> int:
    """Вернуть 0 ок, -1 уже в списке."""
    ids = list(q.participants or [])
    if tg_id in ids:
        return -1
    ids.append(tg_id)
    q.participants = ids
    flag_modified(q, "participants")
    db.commit()
    return 0


def remove_participant(db: Session, q: Queue, tg_id: int) -> None:
    ids = [x for x in (q.participants or []) if x != tg_id]
    q.participants = ids
    flag_modified(q, "participants")
    db.commit()


def delete_queue_row(db: Session, q: Queue) -> None:
    db.delete(q)
    db.commit()


def get_user_display(db: Session, tg_id: int) -> str:
    u = db.query(User).filter(User.tg_id == tg_id).first()
    if u is None:
        return str(tg_id)
    if u.real_name:
        un = f"@{u.tg_username}" if u.tg_username else ""
        return f"{u.real_name} {un}".strip()
    if u.tg_username:
        return f"@{u.tg_username}"
    return str(tg_id)


def change_realname_for_user(db: Session, tg_id: int, new_name: str) -> None:
    u = db.query(User).filter(User.tg_id == tg_id).first()
    if not u:
        raise ValueError("Пользователь не найден")
    u.real_name = new_name
    db.commit()


def ensure_submission_row(db: Session, tg_id: int, subject_id: int) -> SubmissionAttempt:
    s = (
        db.query(SubmissionAttempt)
        .filter(
            SubmissionAttempt.tg_id == tg_id,
            SubmissionAttempt.subject_id == subject_id,
        )
        .first()
    )
    if s:
        return s
    s = SubmissionAttempt(
        tg_id=tg_id,
        subject_id=subject_id,
        history_position=[],
        missed_attempts_count=0,
    )
    db.add(s)
    try:
        db.commit()
        db.refresh(s)
    except IntegrityError:
        db.rollback()
        s = (
            db.query(SubmissionAttempt)
            .filter(
                SubmissionAttempt.tg_id == tg_id,
                SubmissionAttempt.subject_id == subject_id,
            )
            .first()
        )
    return s


def append_one_history_position(
    db: Session, tg_id: int, subject_id: int, position_label: str
) -> None:
    row = ensure_submission_row(db, tg_id, subject_id)
    hp = list(row.history_position or [])
    hp.append(position_label)
    row.history_position = hp
    flag_modified(row, "history_position")
    db.commit()


def add_history_positions(
    db: Session, ordered_tg_ids: list[int], subject_id: int
) -> None:
    for idx, tg_id in enumerate(ordered_tg_ids):
        row = ensure_submission_row(db, tg_id, subject_id)
        hp = list(row.history_position or [])
        hp.append(str(idx + 1))
        row.history_position = hp
        flag_modified(row, "history_position")
    db.commit()


def sync_last_history_positions_after_swap(
    db: Session,
    *,
    subject_id: int,
    first_tg_id: int,
    first_new_pos_1based: int,
    second_tg_id: int,
    second_new_pos_1based: int,
    commit: bool = True,
) -> None:
    """
    После swap в сформированной очереди синхронизирует ПОСЛЕДНЮЮ запись
    history_position для двух участников.

    Схема БД не меняется: просто переписываем последний элемент массива.
    Если истории нет/пуста — запись пропускается.
    """
    pairs = (
        (first_tg_id, first_new_pos_1based),
        (second_tg_id, second_new_pos_1based),
    )
    for tg_id, new_pos in pairs:
        row = (
            db.query(SubmissionAttempt)
            .filter(
                SubmissionAttempt.tg_id == tg_id,
                SubmissionAttempt.subject_id == subject_id,
            )
            .first()
        )
        if not row:
            continue
        hp = list(row.history_position or [])
        if not hp:
            continue
        hp[-1] = str(new_pos)
        row.history_position = hp
        flag_modified(row, "history_position")
    if commit:
        db.commit()


def increment_missed_for_tg_ids(
    db: Session, tg_ids: list[int], subject_id: int
) -> None:
    for tg_id in tg_ids:
        row = ensure_submission_row(db, tg_id, subject_id)
        row.missed_attempts_count = int(row.missed_attempts_count or 0) + 1
    db.commit()


def get_payment_by_yookassa_id(
    db: Session, yookassa_payment_id: str
) -> PaymentRecord | None:
    return (
        db.query(PaymentRecord)
        .filter(PaymentRecord.yookassa_payment_id == yookassa_payment_id)
        .first()
    )


def create_payment_record(
    db: Session,
    yookassa_id: str,
    chat_id: int,
    tier: str,
    amount_rub: str,
    status: str,
    *,
    commit: bool = True,
) -> PaymentRecord:
    p = PaymentRecord(
        yookassa_payment_id=yookassa_id,
        chat_id=chat_id,
        tier=tier,
        amount_rub=amount_rub,
        status=status,
    )
    db.add(p)
    if commit:
        db.commit()
        db.refresh(p)
    return p


def _subscription_access_state(
    chat: Chat, now: datetime
) -> tuple[str, datetime | None]:
    """Текущий «линейный» доступ: trial / base / supervip / expired и конец периода."""
    tier = (chat.subscription_tier or "trial").lower()
    sub_end = chat.subscription_ends_at
    trial_end = chat.trial_ends_at
    if tier == "supervip" and sub_end and sub_end > now:
        return "supervip", sub_end
    if tier == "base" and sub_end and sub_end > now:
        return "base", sub_end
    if trial_end and trial_end > now:
        return "trial", trial_end
    return "expired", None


def _stack_calendar_months_for_purchase(target_tier: str, state: str) -> bool:
    """
    Продление тем же продуктовым рядом: дни суммируются календарно (месяц/год),
    а не через дневную ставку от месячной цены (иначе «год за 1490» даёт ~299 «месячных» дней).
    """
    t = target_tier.lower()
    if t == "base":
        return state in ("base", "trial", "expired")
    if t == "supervip":
        # SuperVIP-покупка из trial тоже должна начисляться календарно
        # (например, svip_12 = +12 месяцев, а не pro-rata в днях).
        return state in ("supervip", "trial", "expired")
    return False


def apply_paid_subscription(
    db: Session,
    chat_id: int,
    tier: str,
    amount_paid: float,
    months: int = 0,
    *,
    commit: bool = True,
) -> None:
    """
    - Один и тот же тариф (Base поверх Base/Trial, SuperVIP поверх SuperVIP): к текущему
      окончанию доступа прибавляются купленные месяцы (1 или 12).
    - Смена тарифа (Base ↔ SuperVIP) или legacy-платежи без months: денежный pro-rata —
      остаток текущего доступа в рублях + оплата, перевод в дни по ставке нового тарифа.
    """
    from src.services.subscription import current_access_deadline

    chat = ensure_chat(db, chat_id)
    now = datetime.utcnow()
    tier_l = tier.lower()

    rates = {
        "base": 149.0,
        "supervip": 249.0,
        "trial": 149.0,
    }

    state, _ = _subscription_access_state(chat, now)
    deadline = current_access_deadline(chat, now)

    if months > 0 and _stack_calendar_months_for_purchase(tier_l, state):
        anchor = max(now, deadline) if deadline else now
        new_deadline = anchor + relativedelta(months=months)
        chat.subscription_tier = tier_l
        chat.subscription_ends_at = new_deadline
        chat.subscription_reminder_state = None
        if commit:
            db.commit()
        return

    # Pro-rata: смена уровня или платёж без явного периода
    remaining_days = 0.0
    if deadline and deadline > now:
        remaining_days = (deadline - now).total_seconds() / 86400.0

    if state == "supervip":
        old_daily = rates["supervip"] / 30.0
    elif state == "base":
        old_daily = rates["base"] / 30.0
    elif state == "trial":
        old_daily = rates["trial"] / 30.0
    else:
        old_daily = 0.0

    current_value = remaining_days * old_daily
    total_value = current_value + float(amount_paid)
    new_daily_rate = rates.get(tier_l, 149.0) / 30.0
    if new_daily_rate <= 0:
        new_daily_rate = 149.0 / 30.0
    new_days = total_value / new_daily_rate
    new_deadline = now + timedelta(days=new_days)

    chat.subscription_tier = tier_l
    chat.subscription_ends_at = new_deadline
    chat.subscription_reminder_state = None
    if commit:
        db.commit()


def apply_upgrade_to_supervip(db: Session, chat_id: int, months: int = 1) -> None:
    # Эта функция больше не нужна, так как apply_paid_subscription теперь универсальна
    # Но оставим заглушку для совместимости, если нужно
    pass


def apply_upgrade_tier_only(
    db: Session, chat_id: int, tier: str, *, commit: bool = True
) -> None:
    """Просто повышает уровень подписки без изменения даты окончания (для пропорциональной доплаты)."""
    chat = ensure_chat(db, chat_id)
    chat.subscription_tier = tier.lower()
    chat.subscription_reminder_state = None
    if commit:
        db.commit()


def find_user_by_username(db: Session, username: str) -> User | None:
    u = username.strip().lstrip("@").lower()
    return (
        db.query(User)
        .filter(func.lower(User.tg_username) == func.lower(u))
        .first()
    )


def list_queues_waiting_last(db: Session):
    return (
        db.query(Queue)
        .filter(Queue.status == "waiting_for_last_participant")
        .all()
    )


def list_queues_recruiting(db: Session):
    return (
        db.query(Queue).filter(Queue.status == "waiting_for_participants").all()
    )


def open_swap(
    db: Session,
    chat_id: int,
    queue_message_id: int,
    subject_id: int,
    from_tg_id: int,
    swap_message_id: int,
) -> SwapRequest:
    sw = SwapRequest(
        chat_id=chat_id,
        queue_message_id=queue_message_id,
        subject_id=subject_id,
        from_tg_id=from_tg_id,
        to_tg_id=None,
        status="open",
        swap_message_id=swap_message_id,
    )
    db.add(sw)
    db.commit()
    db.refresh(sw)
    return sw


def find_open_swap_for_message(db: Session, queue_message_id: int) -> SwapRequest | None:
    """Открытая заявка этапа набора: второй участник ещё не нажал «Поменяться»."""
    return (
        db.query(SwapRequest)
        .filter(
            SwapRequest.queue_message_id == queue_message_id,
            SwapRequest.status == "open",
            SwapRequest.to_tg_id.is_(None),
        )
        .first()
    )


def delete_swaps_pending_for_queue(db: Session, queue_message_id: int) -> None:
    """Удалить незавершённые обмены по этому сообщению очереди."""
    db.execute(
        delete(SwapRequest).where(
            SwapRequest.queue_message_id == queue_message_id,
            SwapRequest.status.in_(("open", "await_accept")),
        )
    )
    db.commit()


def get_swap_request(db: Session, swap_id: int) -> SwapRequest | None:
    return db.query(SwapRequest).filter(SwapRequest.id == swap_id).first()


def delete_swap_request_row(db: Session, sw: SwapRequest) -> None:
    db.delete(sw)
    db.commit()


def create_formed_swap_request(
    db: Session,
    chat_id: int,
    queue_message_id: int,
    subject_id: int,
    from_tg_id: int,
    to_tg_id: int,
    confirm_message_id: int | None = None,
) -> SwapRequest:
    sw = SwapRequest(
        chat_id=chat_id,
        queue_message_id=queue_message_id,
        subject_id=subject_id,
        from_tg_id=from_tg_id,
        to_tg_id=to_tg_id,
        status="await_accept",
        swap_message_id=confirm_message_id,
    )
    db.add(sw)
    db.commit()
    db.refresh(sw)
    return sw


def set_swap_request_message_id(db: Session, sw: SwapRequest, msg_id: int) -> None:
    sw.swap_message_id = msg_id
    db.commit()


def mark_swap_done(db: Session, sw: SwapRequest) -> None:
    sw.status = "done"
    db.commit()


def complete_swap(db: Session, sw: SwapRequest, to_tg_id: int) -> None:
    sw.to_tg_id = to_tg_id
    sw.status = "done"
    db.commit()


def delete_swaps_for_queue(db: Session, queue_message_id: int) -> None:
    db.execute(delete(SwapRequest).where(SwapRequest.queue_message_id == queue_message_id))
    db.commit()
