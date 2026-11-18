from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from app.config import settings
from app.nightwatch.investigator import Investigator
from app.nightwatch.state import state
from app.nightwatch.store import NightwatchStore

router = APIRouter()

_store: NightwatchStore | None = None
_investigator: Investigator | None = None
_stared_at: datetime | None = None


def configure(store: NightwatchStore, investigator: Investigator | None = None) -> None:
    global _store, _investigator, _stared_at
    _store = store
    _investigator = investigator
    _stared_at = datetime.utcnow()


async def _authorize(x_nightwatch_token: str = Header(default="")) -> None:
    expected = settings.nightwatch_proxy_token
    if expected and x_nightwatch_token != expected:
        raise HTTPException(status_code=401, detail="invalid Nightwatch token")


def _require_store() -> NightwatchStore:
    if _store is None:
        raise HTTPException(status_code=503, detail="Nightwatch store is not initialized")
    return _store


def _partition_filter(raw: str) -> set[str] | None:
    """Parse the Agent-provided partition scope header.

    Missing/`*` means unrestricted (machine identity); `__none__` means the
    signed-in user has no readable partitions.
    """
    value = (raw or "").strip()
    if value in {"", "*"}:
        return None
    if value == "__none__":
        return set()
    return {item.strip() for item in value.split(",") if item.strip()}


def _allowed(partition: str, allowed: set[str] | None) -> bool:
    return allowed is None or partition in allowed


def _effective_allowed(
    allowed: set[str] | None, assignments: list[dict]
) -> set[str]:
    """Intersect a user's scope with the partitions Nightwatch actually watches."""
    watched = {
        str(item["partition"])
        for item in assignments
        if item.get("enabled")
    }
    return watched if allowed is None else allowed & watched


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        raise HTTPException(status_code=400, detail="since must be an ISO timestamp")


@router.post("/v1/investigations", dependencies=[Depends(_authorize)])
async def investigate(
    request: Request,
    x_nightwatch_partition_access: str = Header(default=""),
):
    """Run an on-demand investigation for an incident supplied by Doctor.

    This is the analysis provider used when a Doctor incident resolves. It
    reuses the same Investigator as the shadow worker, so the postmortem is
    grounded in real metrics, logs, traces, and topology rather than alert
    metadata alone.
    """
    global _investigator
    if _investigator is None:
        raise HTTPException(status_code=503, detail="investigator is not initialized")
    try:
        payload: dict[str, Any] = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    incident = payload.get("incident") or {}
    alerts = payload.get("alerts") or []
    if not isinstance(incident, dict) or not isinstance(alerts, list):
        raise HTTPException(status_code=400, detail="incident must be an object and alerts a list")
    partition = str(incident.get("partition") or "").strip()
    if not partition:
        raise HTTPException(status_code=400, detail="incident.partition is required")
    allowed = _partition_filter(x_nightwatch_partition_access)
    if not _allowed(partition, allowed):
        raise HTTPException(status_code=403, detail="partition scope denied")
    try:
        deadline = float(payload.get("deadlineSeconds") or 90)
    except (TypeError, ValueError):
        deadline = 90.0
    deadline = max(5.0, min(deadline, 120.0))
    try:
        result = await asyncio.wait_for(
            _investigator.investigate(incident, alerts),
            timeout=deadline,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="investigation timed out")
    return asdict(result)


@router.get("/healthz")
async def healthz():
    return {"status": "ok", "mode": state.mode, "enabled": state.enabled}


@router.get("/ready")
async def ready():
    return {"status": "ready", "store": _store is not None}


@router.get("/v1/health")
async def health():
    return {
        "status": "ok",
        "mode": state.mode,
        "enabled": state.enabled,
        "started_at": state.started_at.isoformat(),
        "last_poll_at": state.last_poll_at.isoformat() if state.last_poll_at else None,
        "last_error": state.last_error,
        "iterations": state.iterations,
        "store_ready": _store is not None,
    }


@router.get("/v1/overview", dependencies=[Depends(_authorize)])
async def overview(x_nightwatch_partition_access: str = Header(default="")):
    store = _require_store()
    allowed = _partition_filter(x_nightwatch_partition_access)
    all_assignments = await store.list_assignments()
    effective = _effective_allowed(allowed, all_assignments)
    assignments = [
        item
        for item in all_assignments
        if _allowed(str(item["partition"]), effective)
    ]
    incidents = [
        item
        for item in await store.list_incidents(limit=200)
        if _allowed(str(item["partition"]), effective)
    ]
    return {
        "mode": state.mode,
        "enabled": state.enabled,
        "assignments": len([item for item in assignments if item["enabled"]]),
        "open_incidents": len([item for item in incidents if item["status"] != "resolved"]),
        "investigating": len([item for item in incidents if item["status"] == "investigating"]),
        "handed_off": len([item for item in incidents if item["handoff_status"] == "handed_off"]),
        "escalated": len(
            [item for item in incidents if item.get("escalation_status") in {"recommended", "escalated"}]
        ),
        "resolved": len([item for item in incidents if item["status"] == "resolved"]),
        "last_poll_at": state.last_poll_at.isoformat() if state.last_poll_at else None,
        "last_error": state.last_error,
    }


@router.get("/v1/brief", dependencies=[Depends(_authorize)])
async def brief(
    since: str | None = Query(default=None),
    user: str = Query(default=""),
    x_nightwatch_partition_access: str = Header(default=""),
):
    store = _require_store()
    allowed = _partition_filter(x_nightwatch_partition_access)
    effective = _effective_allowed(allowed, await store.list_assignments())
    return await store.brief(
        since=_parse_since(since),
        user_subject=user,
        allowed_partitions=effective,
    )


@router.get("/v1/assignments", dependencies=[Depends(_authorize)])
async def assignments(x_nightwatch_partition_access: str = Header(default="")):
    store = _require_store()
    allowed = _partition_filter(x_nightwatch_partition_access)
    return {
        "assignments": [
            item
            for item in await store.list_assignments()
            if _allowed(str(item["partition"]), allowed)
        ]
    }


@router.get("/v1/incidents", dependencies=[Depends(_authorize)])
async def incidents(
    status: str | None = Query(default=None),
    partition: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    x_nightwatch_partition_access: str = Header(default=""),
):
    store = _require_store()
    allowed = _partition_filter(x_nightwatch_partition_access)
    effective = _effective_allowed(allowed, await store.list_assignments())
    return {
        "incidents": [
            item
            for item in await store.list_incidents(
                status=status, partition=partition, limit=limit
            )
            if _allowed(str(item["partition"]), effective)
        ]
    }


@router.get("/v1/incidents/{incident_id}", dependencies=[Depends(_authorize)])
async def incident(
    incident_id: str,
    x_nightwatch_partition_access: str = Header(default=""),
):
    store = _require_store()
    allowed = _partition_filter(x_nightwatch_partition_access)
    effective = _effective_allowed(allowed, await store.list_assignments())
    item = await store.get_incident(incident_id)
    if item is None or not _allowed(str(item.get("partition") or ""), effective):
        raise HTTPException(status_code=404, detail="incident not found")
    return item


@router.post(
    "/v1/incidents/{incident_id}/improvements/{improvement_id}/session",
    dependencies=[Depends(_authorize)],
)
async def improvement_session(incident_id: str, improvement_id: str, request: Request):
    store = _require_store()
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    session_id = str((payload or {}).get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    improvement = await store.get_improvement(improvement_id)
    if improvement is None or improvement["incident_id"] != incident_id:
        raise HTTPException(status_code=404, detail="improvement not found")
    updated = await store.mark_improvement_handoff(improvement_id, session_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="improvement not found")
    return updated


@router.get("/v1/activity", dependencies=[Depends(_authorize)])
async def activity(
    since: str | None = Query(default=None),
    partition: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    x_nightwatch_partition_access: str = Header(default=""),
):
    store = _require_store()
    allowed = _partition_filter(x_nightwatch_partition_access)
    effective = _effective_allowed(allowed, await store.list_assignments())
    return {
        "activity": await store.activity(
            since=_parse_since(since),
            partition=partition,
            allowed_partitions=effective,
            limit=limit,
        )
    }
