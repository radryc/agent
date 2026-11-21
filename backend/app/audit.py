"""Best-effort audit emitter for the Agent plane (EPIC-H).

Events are hash-chained per writer and inserted into the shared ClickHouse
audit.events table. When ``AGENT_AUDIT_CH_URL`` is unset the emitter is inert.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import threading
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


class AuditEmitter:
    def __init__(
        self,
        endpoint: str,
        database: str,
        user: str,
        password: str,
        writer_id: str,
        state_path: str,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._database = database or "audit"
        self._user = user
        self._password = password
        self._writer_id = writer_id
        self._state_path = state_path
        self._lock = threading.Lock()
        self._seq = 0
        self._prev = GENESIS
        self._load_state()

    @classmethod
    def from_env(cls) -> "AuditEmitter | None":
        endpoint = os.environ.get("AGENT_AUDIT_CH_URL", "").strip()
        if not endpoint:
            return None
        writer = os.environ.get("AGENT_AUDIT_WRITER_ID", "").strip()
        if not writer:
            writer = f"agent-{os.uname().nodename}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        state_path = os.environ.get("AGENT_AUDIT_STATE_FILE", "").strip()
        if not state_path:
            state_path = "/data/agent-audit-state.json"
        return cls(
            endpoint=endpoint,
            database=os.environ.get("AGENT_AUDIT_CH_DATABASE", "audit"),
            user=os.environ.get("AGENT_AUDIT_CH_USER", ""),
            password=os.environ.get("AGENT_AUDIT_CH_PASSWORD", ""),
            writer_id=writer,
            state_path=state_path,
        )

    def _load_state(self) -> None:
        try:
            raw = Path(self._state_path).read_text(encoding="utf-8")
            state = json.loads(raw)
        except (OSError, ValueError):
            return
        if str(state.get("writer_id") or "") != self._writer_id:
            return
        try:
            self._seq = int(state.get("seq", 0))
        except (TypeError, ValueError):
            self._seq = 0
        prev = str(state.get("prev_hash") or "").strip()
        if prev:
            self._prev = prev

    def _save_state(self) -> None:
        try:
            Path(self._state_path).parent.mkdir(parents=True, exist_ok=True)
            Path(self._state_path).write_text(
                json.dumps(
                    {
                        "writer_id": self._writer_id,
                        "seq": self._seq,
                        "prev_hash": self._prev,
                    }
                ),
                encoding="utf-8",
            )
        except OSError:
            pass

    def emit(
        self,
        action: str,
        decision: str,
        resource: str,
        *,
        scope: str = "agent",
        reason: str = "",
        method: str = "",
        path: str = "",
        status: int = 0,
        request_id: str = "",
        subject: str = "",
        detail: str = "",
        resource_type: str = "tool",
    ) -> None:
        if not self._endpoint:
            return
        with self._lock:
            event: dict[str, Any] = {
                "timestamp": datetime.datetime.now(datetime.timezone.utc)
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z"),
                "event_id": str(uuid.uuid4()),
                "writer_id": self._writer_id,
                "seq": self._seq,
                "prev_hash": self._prev,
                "service": "agent",
                "env": os.environ.get("AUDIT_ENV", "").strip(),
                "scope": scope,
                "action": action,
                "decision": decision,
                "reason": reason,
                "resource_type": resource_type,
                "actor_subject": subject,
                "resource": resource,
                "method": method,
                "path": path,
                "status": status,
                "request_id": request_id,
                "detail": detail,
            }
            canonical = json.dumps(event, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256((self._prev + canonical).encode("utf-8")).hexdigest()
            event["hash"] = digest
            event["raw_json"] = canonical
            # Retry the same event/seq so an ambiguous ClickHouse success
            # cannot make the next event reuse the sequence with a new hash.
            inserted = False
            for attempt in range(3):
                try:
                    self._insert(event)
                    inserted = True
                    break
                except Exception:
                    time.sleep(0.1 * (attempt + 1))
            if not inserted:
                # Audit must not break the Agent request path; the gap is
                # visible in the emitter metrics and anchor count.
                return
            self._seq += 1
            self._prev = digest
            self._save_state()

    def _insert(self, event: dict[str, Any]) -> None:
        body = (json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8")
        url = (
            f"{self._endpoint}/?query=INSERT%20INTO%20{self._database}.events%20FORMAT%20JSONEachRow"
            "&date_time_input_format=best_effort"
        )
        request = urllib.request.Request(url, data=body, method="POST")
        request.add_header("Content-Type", "application/x-ndjson")
        if self._user or self._password:
            import base64

            token = base64.b64encode(f"{self._user}:{self._password}".encode()).decode()
            request.add_header("Authorization", f"Basic {token}")
        with urllib.request.urlopen(request, timeout=5):
            pass

    def close(self) -> None:
        self._save_state()


_emitter: AuditEmitter | None = None
_emitter_lock = threading.Lock()


def get_emitter() -> AuditEmitter | None:
    global _emitter
    if _emitter is None and _env_bool("AGENT_AUDIT_ENABLED", True):
        with _emitter_lock:
            if _emitter is None:
                _emitter = AuditEmitter.from_env()
    return _emitter


def emit(action: str, decision: str, resource: str, **fields: Any) -> None:
    emitter = get_emitter()
    if emitter is not None:
        emitter.emit(action, decision, resource, **fields)
