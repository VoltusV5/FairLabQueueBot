"""Фасад для запросов к БД.

Объединяет все функции из репозиториев для обратной совместимости.
"""

from __future__ import annotations

from src.db.repositories.chat import (
    _stack_calendar_months_for_purchase,
    _subscription_access_state,
    add_chat_admin,
    apply_paid_subscription,
    ensure_chat,
    get_chat,
    is_chat_admin,
    list_all_chats,
    remove_chat_admin,
    set_chat_autoclose_rules,
)
from src.db.repositories.payment import (
    create_payment_record,
    get_payment_by_yookassa_id,
)
from src.db.repositories.presence import (
    get_presence_poll,
    upsert_presence_here,
)
from src.db.repositories.queue import (
    ParticipantAlreadyExistsError,
    ParticipantNotFoundError,
    QueueError,
    QueueStatusError,
    add_participant,
    add_queue_row,
    complete_queue_last_submitter,
    delete_queue_row,
    get_queue_by_chat_message,
    insert_into_formed_queue,
    is_queue_duplicate,
    list_queues_recruiting,
    list_queues_waiting_last,
    merge_extra,
    pardon_queue_participant,
    remove_participant,
    rollback_and_delete_queue,
)
from src.db.repositories.subject import (
    add_subject_king,
    get_or_create_subject,
    get_subject_by_id,
    get_subject_by_name,
    list_subject_names_for_chat,
    remove_subject_king,
)
from src.db.repositories.submission import (
    HistoryEntry,
    add_history_positions,
    append_one_history_position,
    apply_slot_penalties_after_last_submitter,
    ensure_submission_row,
    ensure_submission_rows,
    get_entry_pos,
    get_entry_status,
    increment_missed_for_tg_ids,
    shift_last_history_positions_after_insert,
    sync_last_history_positions_after_swap,
)
from src.db.repositories.swap import (
    complete_swap,
    create_formed_swap_request,
    delete_swap_request_row,
    delete_swaps_for_queue,
    delete_swaps_pending_for_queue,
    find_open_swap_for_message,
    get_swap_request,
    mark_swap_done,
    open_swap,
    set_swap_request_message_id,
)
from src.db.repositories.user import (
    change_realname_for_user,
    ensure_user,
    find_user_by_username,
    get_user_display,
    get_users_display_map,
)

__all__ = [
    # chat
    "_stack_calendar_months_for_purchase",
    "_subscription_access_state",
    "add_chat_admin",
    "apply_paid_subscription",
    "ensure_chat",
    "get_chat",
    "is_chat_admin",
    "list_all_chats",
    "remove_chat_admin",
    "set_chat_autoclose_rules",
    # payment
    "create_payment_record",
    "get_payment_by_yookassa_id",
    # presence
    "get_presence_poll",
    "upsert_presence_here",
    # queue
    "QueueError",
    "QueueStatusError",
    "ParticipantNotFoundError",
    "ParticipantAlreadyExistsError",
    "add_participant",
    "add_queue_row",
    "complete_queue_last_submitter",
    "delete_queue_row",
    "get_queue_by_chat_message",
    "insert_into_formed_queue",
    "is_queue_duplicate",
    "list_queues_recruiting",
    "list_queues_waiting_last",
    "merge_extra",
    "pardon_queue_participant",
    "remove_participant",
    "rollback_and_delete_queue",
    # subject
    "add_subject_king",
    "get_or_create_subject",
    "get_subject_by_id",
    "get_subject_by_name",
    "list_subject_names_for_chat",
    "remove_subject_king",
    # submission
    "HistoryEntry",
    "add_history_positions",
    "append_one_history_position",
    "apply_slot_penalties_after_last_submitter",
    "ensure_submission_row",
    "ensure_submission_rows",
    "get_entry_pos",
    "get_entry_status",
    "increment_missed_for_tg_ids",
    "shift_last_history_positions_after_insert",
    "sync_last_history_positions_after_swap",
    # swap
    "complete_swap",
    "create_formed_swap_request",
    "delete_swap_request_row",
    "delete_swaps_for_queue",
    "delete_swaps_pending_for_queue",
    "find_open_swap_for_message",
    "get_swap_request",
    "mark_swap_done",
    "open_swap",
    "set_swap_request_message_id",
    # user
    "change_realname_for_user",
    "ensure_user",
    "find_user_by_username",
    "get_user_display",
    "get_users_display_map",
]
