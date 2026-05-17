from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.agent import session as session_mgr
from app.models.schemas import SessionUpdate

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("")
async def list_sessions():
    return await session_mgr.list_sessions()


@router.post("")
async def create_session():
    return await session_mgr.create_session()


@router.get("/{session_id}")
async def get_session(session_id: str):
    result = await session_mgr.get_session(session_id)
    if not result:
        raise HTTPException(status_code=404, detail="Session not found")
    return result


@router.delete("/{session_id}")
async def delete_session(session_id: str):
    ok = await session_mgr.delete_session(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True}


@router.patch("/{session_id}")
async def update_session(session_id: str, body: SessionUpdate):
    result = await session_mgr.update_session_title(session_id, body.title)
    if not result:
        raise HTTPException(status_code=404, detail="Session not found")
    return result
