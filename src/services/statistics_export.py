"""Текстовый отчёт по статистике пользователя."""

from __future__ import annotations

from io import BytesIO
from sqlalchemy.orm import Session

from src.db.init_db import Subject, SubmissionAttempt, User


def build_user_stats_text(db: Session, tg_id: int) -> str:
    u = db.query(User).filter(User.tg_id == tg_id).first()
    lines = []
    if u:
        lines.append(f"👤 Пользователь: {u.real_name or '—'}")
        if u.tg_username:
            lines.append(f"🔗 Telegram: @{u.tg_username}")
    else:
        lines.append(f"🆔 ID: {tg_id} (нет в базе)")
    lines.append("")
    rows = (
        db.query(SubmissionAttempt, Subject)
        .join(Subject, Subject.id == SubmissionAttempt.subject_id)
        .filter(SubmissionAttempt.tg_id == tg_id)
        .all()
    )
    if not rows:
        lines.append("История очередей пуста.")
        return "\n".join(lines)
    lines.append("📊 Статистика по предметам:")
    for sa, subj in rows:
        hp = sa.history_position or []
        successful_hp = [int(x) for x in hp if not str(x).endswith("M")]
        approaches = len(successful_hp)
        missed = sa.missed_attempts_count
        avg_pos = sum(successful_hp) / approaches if approaches else 0
        lines.append(f"\n📘 {subj.subject_name}:")
        lines.append(f"  └ ✅ Успешных сдач: {approaches}")
        lines.append(f"  └ ⚠️ Пропусков/отказов: {missed}")
        if approaches:
            lines.append(f"  └ 📈 Средняя позиция: {avg_pos:.1f}")
    return "\n".join(lines)


def stats_file_bytes(tg_id: int, text: str) -> BytesIO:
    bio = BytesIO(text.encode("utf-8"))
    bio.name = f"stat_{tg_id}.txt"
    return bio
