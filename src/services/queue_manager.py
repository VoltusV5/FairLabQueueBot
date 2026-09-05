"""Формирование порядка очереди по tg_id и истории попыток."""

from __future__ import annotations

import html
from random import randint
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from src.db import queries as queries_db
from src.db.init_db import SubmissionAttempt


async def order_tg_ids(
    db: AsyncSession,
    participant_tg_ids: list[int | str],
    subject_id: int,
    chat_id: int | None = None,
) -> list[int | str]:
    """Формирует итоговый справедливый порядок участников в очереди.

    Принцип ранжирования:
    1. Группы: участники одной группы идут совместным блоком.
       Приоритет блока = среднее количество успешных сдач участников.
    2. Внутри группы или для одиночек:
       - Меньше подходов (approaches);
       - Больше пропусков (missed);
       - Больше средняя позиция (avg_pos);
       - Случайный tie-breaker для разрешения коллизий.

    Args:
        db: Асинхронная сессия базы данных.
        participant_tg_ids: Список идентификаторов участников очереди.
        subject_id: Идентификатор учебного предмета.
        chat_id: Идентификатор чата (для учета сформированных групп).

    Returns:
        list[int | str]: Отсортированный список идентификаторов участников.
    """
    unique_ids = list(dict.fromkeys(participant_tg_ids))
    real_uids = [
        int(uid) for uid in unique_ids if isinstance(uid, int) and uid > 0
    ]

    # Пакетная загрузка истории без N+1 вызовов
    sub_map: dict[int, SubmissionAttempt] = {}
    if real_uids:
        sub_map = await queries_db.ensure_submission_rows(
            db, real_uids, subject_id
        )

    # Подготовка метрик для каждого участника
    user_data: dict[int | str, dict[str, Any]] = {}
    for uid in unique_ids:
        row = (
            sub_map.get(int(uid)) if isinstance(uid, int) and uid > 0 else None
        )
        if row:
            hp = row.history_position or []
            successful_hp = [
                queries_db.get_entry_pos(x)
                for x in hp
                if queries_db.get_entry_status(x) == "submitted"
            ]
            missed_hp = [
                queries_db.get_entry_pos(x)
                for x in hp
                if queries_db.get_entry_status(x) == "missed"
            ]

            approaches = len(successful_hp)
            if approaches == 0:
                avg_pos = sum(missed_hp) / len(missed_hp) if missed_hp else 0.0
                sort_avg_pos = avg_pos
            else:
                avg_pos = (
                    sum(successful_hp) / len(successful_hp)
                    if successful_hp
                    else 0.0
                )
                sort_avg_pos = -avg_pos
            missed_cnt = row.missed_attempts_count or 0
        else:
            approaches = 0
            missed_cnt = 0
            sort_avg_pos = 0.0

        user_data[uid] = {
            "approaches": approaches,
            "missed": missed_cnt,
            "sort_avg_pos": sort_avg_pos,
            "tie": randint(0, 100_000),
        }

    # Группы чата
    groups: list[list[int]] = []
    if chat_id:
        chat = await queries_db.get_chat(db, chat_id)
        if chat and chat.groups:
            groups = chat.groups

    # Формирование блоков: сгруппированные участники и одиночки
    assigned_uids: set[int | str] = set()
    blocks: list[tuple[tuple[float, int, float, int], list[int | str]]] = []

    for g in groups:
        members: list[int | str] = [uid for uid in g if uid in unique_ids]
        if not members:
            continue
        g_approaches = sum(user_data[m]["approaches"] for m in members) / len(
            members
        )
        blocks.append(((float(g_approaches), -1, 0.0, 0), members))
        assigned_uids.update(members)

    # Одиночки
    for uid in unique_ids:
        if uid not in assigned_uids:
            d = user_data[uid]
            blocks.append(
                (
                    (
                        float(d["approaches"]),
                        -int(d["missed"]),
                        float(d["sort_avg_pos"]),
                        int(d["tie"]),
                    ),
                    [uid],
                )
            )

    # Сортируем блоки по вычисленному приоритету
    blocks.sort(key=lambda x: x[0])

    # Сборка финального упорядоченного списка
    final_order: list[int | str] = []
    for _, uids in blocks:
        if len(uids) > 1:
            uids.sort(
                key=lambda u: (
                    user_data[u]["approaches"],
                    -user_data[u]["missed"],
                    user_data[u]["sort_avg_pos"],
                    user_data[u]["tie"],
                )
            )
        final_order.extend(uids)

    return final_order


async def format_queue_lines(
    db: AsyncSession,
    ordered_tg_ids: list[int | str],
    refused_slot_indices: set[int],
    kings: list[int] | None = None,
    temp_names: dict[str, str] | None = None,
) -> str:
    """Форматирует строковое представление списка участников очереди.

    Пакетно загружает отображаемые имена пользователей, помечает королей
    бригад и отказавшихся участников.

    Args:
        db: Асинхронная сессия базы данных.
        ordered_tg_ids: Упорядоченный список идентификаторов участников.
        refused_slot_indices: Набор индексов позиций с пометкой об отказе.
        kings: Список Telegram ID королей бригад.
        temp_names: Словарь временных имен для незарегистрированных записей.

    Returns:
        str: Готовое текстовое тело списка очереди с HTML-разметкой.
    """
    kings_list = kings or []
    temp_dict = temp_names or {}

    display_map = await queries_db.get_users_display_map(db, ordered_tg_ids)

    lines: list[str] = []
    for idx, entry in enumerate(ordered_tg_ids):
        is_king = False
        str_entry = str(entry)
        if isinstance(entry, int) and entry < 0 and str_entry in temp_dict:
            label = temp_dict[str_entry]
        elif str_entry in temp_dict:
            label = temp_dict[str_entry]
        else:
            label = display_map.get(entry, str_entry)
            is_king = isinstance(entry, int) and entry in kings_list

        escaped_label = html.escape(label, quote=False)
        suffix = (
            " (отказался от участия в очереди)"
            if idx in refused_slot_indices
            else ""
        )
        king_suffix = " 👑" if is_king else ""
        lines.append(f"{idx + 1}. {escaped_label}{king_suffix}{suffix}")

    return "\n".join(lines) + ("\n" if lines else "")


async def append_formation_history(
    db: AsyncSession,
    ordered_tg_ids: list[int | str],
    subject_id: int,
) -> None:
    """Записывает позиции текущего формирования очереди в историю сдачи.

    Args:
        db: Асинхронная сессия базы данных.
        ordered_tg_ids: Упорядоченный список идентификаторов участников.
        subject_id: Идентификатор учебного предмета.
    """
    real_ids = [
        int(uid) for uid in ordered_tg_ids if isinstance(uid, int) and uid > 0
    ]
    if real_ids:
        await queries_db.add_history_positions(db, real_ids, subject_id)
