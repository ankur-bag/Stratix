from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Provide timezone-aware time without coupling business logic to wall time."""

    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
