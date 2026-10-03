"""Immutable dictation results retained only for the current application session."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

MAX_HISTORY_ENTRIES = 30


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    id: int
    created_at: datetime
    text: str
    refined: bool
    elapsed_seconds: float
