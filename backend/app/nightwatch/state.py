from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class WorkerState:
    mode: str = "shadow"
    enabled: bool = True
    started_at: datetime = field(default_factory=datetime.utcnow)
    last_poll_at: datetime | None = None
    last_error: str = ""
    iterations: int = 0


state = WorkerState()
