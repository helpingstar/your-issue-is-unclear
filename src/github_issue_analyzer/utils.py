from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime


FREE_TEXT_ANSWER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*\s*:")


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def is_command_comment(body: str) -> bool:
    normalized = body.strip().lower()
    return normalized.startswith("/refresh") or normalized.startswith("/stop")


def is_free_text_answer_comment(body: str) -> bool:
    return bool(FREE_TEXT_ANSWER_RE.match(body.strip()))


def ensure_utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
