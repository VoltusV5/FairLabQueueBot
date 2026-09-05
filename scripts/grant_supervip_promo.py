#!/usr/bin/env python3
"""Promo upgrade: trial -> supervip for old chats.

Rule:
- If chat was created before cutoff date, and currently has trial tier,
  replace trial with SuperVIP for 1 calendar month.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from dateutil.relativedelta import relativedelta

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.db.db import get_db  # noqa: E402
from src.db.init_db import Chat  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Парсинг аргументов командной строки."""
    parser = argparse.ArgumentParser(
        description=(
            "Grant 1 month SuperVIP instead of trial for chats "
            "created before cutoff date."
        )
    )
    parser.add_argument(
        "--cutoff-date",
        default="2026-04-19",
        help="Cutoff date in YYYY-MM-DD (default: 2026-04-19).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes. Without this flag script runs in dry-run mode.",
    )
    return parser.parse_args()


def main() -> int:
    """Основная функция выполнения промо-скрипта."""
    args = parse_args()
    cutoff = datetime.strptime(args.cutoff_date, "%Y-%m-%d").replace(
        tzinfo=UTC
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    cutoff_naive = cutoff.replace(tzinfo=None)
    new_deadline = now + relativedelta(months=1)

    with get_db() as db:
        candidates: list[Chat] = (
            db.query(Chat)
            .filter(
                Chat.created_at < cutoff_naive,
                Chat.subscription_tier == "trial",
            )
            .all()
        )

        print(
            f"[promo] cutoff<{args.cutoff_date}, trial chats found: "
            f"{len(candidates)}"
        )
        for chat in candidates:
            title = (chat.title or "").strip()
            print(
                f"  - chat_id={chat.chat_id} created_at={chat.created_at} "
                f"title={title[:80]!r}"
            )

        if not args.apply:
            print(
                "[promo] dry-run only. Re-run with --apply to persist changes."
            )
            return 0

        for chat in candidates:
            chat.subscription_tier = "supervip"
            chat.subscription_ends_at = new_deadline
            chat.trial_ends_at = None
            chat.subscription_reminder_state = None

        db.commit()
        print(
            f"[promo] applied: {len(candidates)} chats upgraded to supervip "
            f"until {new_deadline.isoformat()} UTC"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
