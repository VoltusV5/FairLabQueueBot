"""Состояние в памяти: отложенные отмены подтверждений."""

from __future__ import annotations

import asyncio
from typing import Any

# (chat_id, message_id) -> {"task": asyncio.Task, "kind": str}
pending_confirmations: dict[tuple[int, int], dict[str, Any]] = {}

