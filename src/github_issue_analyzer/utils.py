from __future__ import annotations

import hashlib
from datetime import UTC, datetime


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def is_command_comment(body: str) -> bool:
    normalized = body.strip().lower()
    return normalized.startswith("/refresh") or normalized.startswith("/stop")


def is_free_text_answer_comment(body: str) -> bool:
    stripped = body.strip()
    return stripped.startswith("Q") and ":" in stripped


def ensure_utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
