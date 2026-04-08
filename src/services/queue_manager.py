"""
Формирование порядка очереди по tg_id и истории попыток.
"""

from __future__ import annotations

from random import randint

from sqlalchemy.orm import Session

from ..db import queries as Q


def order_tg_ids(db: Session, participant_tg_ids: list[int], subject_id: int, chat_id: int | None = None) -> list[int]:
    """
    Порядок в финальной очереди:
    1. Группы: если участники в одной группе, они идут блоком.
       Приоритет группы = среднее (approaches) её участников.
    2. Внутри группы или для одиночек:
       - Меньше подходов (approaches)
       - Больше пропусков (missed)
       - Больше средняя позиция (avg_pos)
       - Рандом
    """
    unique_ids = list(dict.fromkeys(participant_tg_ids))
    
    # Загружаем данные участников
    user_data = {}
    for uid in unique_ids:
        row = Q.ensure_submission_row(db, uid, subject_id)
        hp = row.history_position or []
        user_data[uid] = {
            "approaches": len(hp),
            "missed": row.missed_attempts_count or 0,
            "avg_pos": sum(int(x) for x in hp) / len(hp) if hp else 0.0,
            "tie": randint(0, 100_000)
        }

    # Группы чата
    groups = []
    if chat_id:
        chat = Q.get_chat(db, chat_id)
        if chat and chat.groups:
            groups = chat.groups

    # Определяем, кто в какой группе (только те, кто записался)
    assigned_uids = set()
    blocks = [] # Список (priority_tuple, [uids])
    
    for g in groups:
        members = [uid for uid in g if uid in unique_ids]
        if not members:
            continue
        # Приоритет группы = среднее число подходов
        g_approaches = sum(user_data[m]["approaches"] for m in members) / len(members)
        # Для сортировки блоков используем тот же принцип
        # (g_approaches, -g_missed, ...) - но упростим до подходов
        blocks.append(((g_approaches, -1, 0, 0), members))
        assigned_uids.update(members)
    
    # Одиночки
    for uid in unique_ids:
        if uid not in assigned_uids:
            d = user_data[uid]
            blocks.append(((d["approaches"], -d["missed"], -d["avg_pos"], d["tie"]), [uid]))
            
    # Сортируем блоки
    blocks.sort(key=lambda x: x[0])
    
    # Собираем финальный список
    final_order = []
    for _, uids in blocks:
        # Внутри группы или для одиночки (блок из 1) сохраняем порядок
        # Но если это группа, внутри неё тоже можно отсортировать по личным заслугам,
        # чтобы внутри блока был порядок по подходам/пропускам.
        if len(uids) > 1:
            uids.sort(key=lambda u: (user_data[u]["approaches"], -user_data[u]["missed"], -user_data[u]["avg_pos"], user_data[u]["tie"]))
        final_order.extend(uids)
        
    return final_order


def format_queue_lines(
    db: Session, ordered_tg_ids: list[int | str], refused_slot_indices: set[int]
) -> str:
    lines = []
    for idx, entry in enumerate(ordered_tg_ids):
        if isinstance(entry, int):
            label = Q.get_user_display(db, entry)
        else:
            # Временный участник (строка)
            label = str(entry)
        # Экранируем спецсимволы HTML
        label = label.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        suffix = " (отказался от участия в очереди)" if idx in refused_slot_indices else ""
        lines.append(f"{idx + 1}. {label}{suffix}")
    return "\n".join(lines) + ("\n" if lines else "")


def append_formation_history(db: Session, ordered_tg_ids: list[int], subject_id: int) -> None:
    """Записать позиции текущего формирования в историю."""
    Q.add_history_positions(db, ordered_tg_ids, subject_id)
