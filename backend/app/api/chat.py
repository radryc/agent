from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.agent.loop import run_agent_turn, ensure_session
from app.agent.opencode import run_opencode, is_opencode_available
from app.models.schemas import ChatRequest

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/chat")
async def chat(req: ChatRequest):
    session_id = await ensure_session(req.session_id)
    result = await run_agent_turn(req.message, session_id, stream=False)
    return {
        "session_id": session_id,
        "message": {"role": "assistant", "content": result},
    }


@router.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket):
    await websocket.accept()
    logger.info("WebSocket client connected")

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "data": "Invalid JSON"})
                continue

            msg_type = data.get("type", "")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if msg_type != "message":
                continue

            user_message = data.get("data", "")
            session_id = data.get("session_id")
            agent = data.get("agent", "local")

            if not user_message:
                await websocket.send_json(
                    {"type": "error", "data": "Empty message"}
                )
                continue

            session_id = await ensure_session(session_id)

            await websocket.send_json(
                {"type": "session", "session_id": session_id}
            )

            try:
                if agent == "opencode":
                    await _stream_opencode(websocket, user_message, session_id)
                else:
                    await _stream_local(websocket, user_message, session_id)
            except Exception as e:
                logger.exception("Error during agent turn")
                await websocket.send_json(
                    {"type": "error", "data": str(e), "session_id": session_id}
                )

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")


async def _stream_local(websocket: WebSocket, user_message: str, session_id: str):
    token_stream = await run_agent_turn(user_message, session_id, stream=True)
    async for token in token_stream:
        await websocket.send_json(
            {"type": "token", "data": token, "session_id": session_id}
        )
    await websocket.send_json({"type": "done", "session_id": session_id})


async def _stream_opencode(websocket: WebSocket, user_message: str, session_id: str):
    if not await is_opencode_available():
        async for token in run_opencode(user_message, session_id):
            await websocket.send_json(
                {"type": "token", "data": token, "session_id": session_id}
            )
        await websocket.send_json({"type": "done", "session_id": session_id})
        return

    async for chunk in run_opencode(user_message, session_id):
        await websocket.send_json(
            {"type": "token", "data": chunk, "session_id": session_id}
        )
    await websocket.send_json({"type": "done", "session_id": session_id})
