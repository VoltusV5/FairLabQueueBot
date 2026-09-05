"""Формирование текстового отчёта по индивидуальной статистике студента."""

from __future__ import annotations

from io import BytesIO

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.init_db import Subject, SubmissionAttempt, User
from src.db.repositories.submission import get_entry_pos, get_entry_status


async def build_user_stats_text(db: AsyncSession, tg_id: int) -> str:
    """Формирует текстовый отчёт о попытках сдачи лабораторных работ.

    Args:
        db: Асинхронная сессия базы данных.
        tg_id: Telegram ID студента.

    Returns:
        str: Форматированный текстовый отчёт по всем предметам.
    """
    user_stmt = select(User).where(User.tg_id == tg_id)
    user_res = await db.execute(user_stmt)
    u = user_res.scalar_one_or_none()

    lines: list[str] = []
    if u:
        lines.append(f"👤 Пользователь: {u.real_name or '—'}")
        if u.tg_username:
            lines.append(f"🔗 Telegram: @{u.tg_username}")
    else:
        lines.append(f"🆔 ID: {tg_id} (нет в базе)")
    lines.append("")

    stmt = (
        select(SubmissionAttempt, Subject)
        .join(Subject, Subject.id == SubmissionAttempt.subject_id)
        .where(SubmissionAttempt.tg_id == tg_id)
        .order_by(Subject.subject_name)
    )
    res = await db.execute(stmt)
    rows = res.all()

    if not rows:
        lines.append("История очередей пуста.")
        return "\n".join(lines)

    lines.append("📊 Статистика по предметам:")
    for sa, subj in rows:
        hp = sa.history_position or []
        successful_hp = [
            get_entry_pos(x) for x in hp if get_entry_status(x) == "submitted"
        ]
        approaches = len(successful_hp)
        missed = sa.missed_attempts_count or 0
        avg_pos = sum(successful_hp) / approaches if approaches else 0.0
        lines.append(f"\n📘 {subj.subject_name}" + ":")
        lines.append(f"  └ ✅ Успешных сдач: {approaches}")
        lines.append(f"  └ ⚠️ Пропусков/отказов: {missed}")
        if approaches:
            avg_str = str(round(avg_pos, 1))
            lines.append("  └ 📈 Средняя позиция: " + avg_str)

    return "\n".join(lines)


def stats_file_bytes(tg_id: int, text: str) -> BytesIO:
    """Упаковывает текст отчёта в файловый поток BytesIO для отправки ботом.

    Args:
        tg_id: Telegram ID студента.
        text: Текстовое содержимое отчёта.

    Returns:
        BytesIO: Буфер байтов с именем файла.
    """
    bio = BytesIO(text.encode("utf-8"))
    bio.name = f"stat_{tg_id}.txt"
    return bio
