from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.config import settings
from app.nightwatch.state import state
from app.nightwatch.store import NightwatchStore

router = APIRouter()

_store: NightwatchStore | None = None
_stared_at: datetime | None = None


def configure(store: NightwatchStore) -> None:
    global _store, _stared_at
    _store = store
    _stared_at = datetime.utcnow()


async def _authorize(x_nightwatch_token: str = Header(default="")) -> None:
    expected = settings.nightwatch_proxy_token
    if expected and x_nightwatch_token != expected:
        raise HTTPException(status_code=401, detail="invalid Nightwatch token")


def _require_store() -> NightwatchStore:
    if _store is None:
        raise HTTPException(status_code=503, detail="Nightwatch store is not initialized")
    return _store


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        raise HTTPException(status_code=400, detail="since must be an ISO timestamp")


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
async def overview():
    store = _require_store()
    assignments = await store.list_assignments()
    incidents = await store.list_incidents(limit=200)
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
):
    store = _require_store()
    return await store.brief(since=_parse_since(since), user_subject=user)


@router.get("/v1/assignments", dependencies=[Depends(_authorize)])
async def assignments():
    store = _require_store()
    return {"assignments": await store.list_assignments()}


@router.get("/v1/incidents", dependencies=[Depends(_authorize)])
async def incidents(
    status: str | None = Query(default=None),
    partition: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    store = _require_store()
    return {"incidents": await store.list_incidents(status=status, partition=partition, limit=limit)}


@router.get("/v1/incidents/{incident_id}", dependencies=[Depends(_authorize)])
async def incident(incident_id: str):
    store = _require_store()
    item = await store.get_incident(incident_id)
    if item is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return item


@router.get("/v1/activity", dependencies=[Depends(_authorize)])
async def activity(
    since: str | None = Query(default=None),
    partition: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    store = _require_store()
    return {
        "activity": await store.activity(
            since=_parse_since(since),
            partition=partition,
            limit=limit,
        )
    }
