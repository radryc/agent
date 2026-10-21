from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import signal

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.agent.loop import run_agent_turn, ensure_session
from app.agent.opencode import run_opencode
from app.agent.personas import (
    DEFAULT_PERSONA_ID,
    get_persona,
    persona_system_prompt,
    resolve_engine,
)
from app.agent.router import resolve_persona, strip_mention
from app.agent import session as session_mgr
from app.agent.workspace import mount_active, mount_root
from app.terminal.exec import execute_command, get_terminal_manager
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
    terminal_manager = get_terminal_manager()
    send_lock = asyncio.Lock()
    terminal_queue: asyncio.Queue[dict] | None = None
    terminal_sender_task: asyncio.Task | None = None
    terminal_session_id: str | None = None

    async def safe_send(message: dict):
        async with send_lock:
            await websocket.send_json(message)

    async def stop_terminal_streaming():
        nonlocal terminal_queue, terminal_sender_task, terminal_session_id
        if terminal_queue is not None and terminal_session_id:
            await terminal_manager.unsubscribe(terminal_session_id, terminal_queue)
        if terminal_sender_task:
            terminal_sender_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await terminal_sender_task
        terminal_queue = None
        terminal_sender_task = None
        terminal_session_id = None

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await safe_send({"type": "error", "data": "Invalid JSON"})
                continue

            msg_type = data.get("type", "")

            if msg_type == "ping":
                await safe_send({"type": "pong"})
                continue

            if msg_type == "terminal_start":
                requested_session_id = await ensure_session(data.get("session_id"))
                await safe_send({"type": "session", "session_id": requested_session_id})

                if requested_session_id != terminal_session_id:
                    await stop_terminal_streaming()
                    terminal_queue = await terminal_manager.subscribe(requested_session_id)
                    terminal_session_id = requested_session_id

                    async def sender(queue: asyncio.Queue[dict]):
                        while True:
                            msg = await queue.get()
                            await safe_send(msg)

                    terminal_sender_task = asyncio.create_task(sender(terminal_queue))
                continue

            if msg_type == "terminal_input":
                if not terminal_session_id:
                    await safe_send({"type": "terminal_error", "data": "Terminal is not started"})
                    continue
                await terminal_manager.write(terminal_session_id, data.get("data", ""))
                continue

            if msg_type == "terminal_resize":
                if not terminal_session_id:
                    continue
                cols = int(data.get("cols", 0) or 0)
                rows = int(data.get("rows", 0) or 0)
                if cols > 0 and rows > 0:
                    await terminal_manager.resize(terminal_session_id, cols, rows)
                continue

            if msg_type == "terminal_signal":
                if not terminal_session_id:
                    continue
                signal_name = str(data.get("signal", "SIGINT"))
                sig = getattr(signal, signal_name, signal.SIGINT)
                await terminal_manager.signal(terminal_session_id, sig)
                continue

            if msg_type == "terminal_history":
                if not terminal_session_id:
                    continue
                await terminal_manager.add_history(terminal_session_id, str(data.get("command", "")))
                continue

            if msg_type == "exec":
                user_message = data.get("data", "")
                session_id = data.get("session_id")
                if not user_message:
                    await safe_send({"type": "error", "data": "Empty command"})
                    continue
                session_id = await ensure_session(session_id)
                await safe_send({"type": "session", "session_id": session_id})
                try:
                    async for msg in execute_command(user_message, session_id):
                        await safe_send(msg)
                except Exception as e:
                    logger.exception("Error executing command")
                    await safe_send({"type": "error", "data": str(e)})
                continue

            if msg_type != "message":
                continue

            user_message = data.get("data", "")
            session_id = data.get("session_id")
            persona_request = _normalize_persona_request(data)

            if not user_message:
                await safe_send(
                    {"type": "error", "data": "Empty message"}
                )
                continue

            session_id = await ensure_session(session_id)

            await safe_send(
                {"type": "session", "session_id": session_id}
            )

            try:
                await _handle_agent_message(safe_send, user_message, session_id, persona_request)
            except Exception as e:
                logger.exception("Error during agent turn")
                await safe_send(
                    {"type": "error", "data": str(e), "session_id": session_id}
                )

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    finally:
        await stop_terminal_streaming()


def _normalize_persona_request(data: dict) -> str:
    """Resolve the requested persona from the WS payload (new + legacy shapes)."""
    persona = data.get("persona")
    if persona is None:
        # Legacy clients sent agent: "local" | "opencode".
        legacy = str(data.get("agent") or "").strip().lower()
        persona = {"opencode": "steve", "local": "otto"}.get(legacy)
    if persona is None:
        return "auto"
    persona = str(persona).strip().lower()
    if persona in ("auto", "otto", "steve"):
        return persona
    return "auto"


async def _handle_agent_message(
    send_json,
    user_message: str,
    session_id: str,
    persona_request: str,
) -> None:
    session = await session_mgr.get_session(session_id)
    active_persona = (session or {}).get("active_persona")

    persona_id, reason = await resolve_persona(
        requested=persona_request,
        active_persona=active_persona,
        message=user_message,
    )
    persona = get_persona(persona_id) or get_persona(DEFAULT_PERSONA_ID)
    engine = await resolve_engine(persona.id)
    await session_mgr.set_active_persona(session_id, persona.id)

    await send_json(
        {
            "type": "persona",
            "data": persona.id,
            "name": persona.name,
            "role": persona.role,
            "avatar": persona.avatar,
            "accent": persona.accent,
            "engine": engine.id,
            "engine_label": engine.label,
            "reason": reason,
            "session_id": session_id,
        }
    )

    logger.info(
        "Routing turn to persona=%s engine=%s reason=%s session=%s",
        persona.id,
        engine.id,
        reason,
        session_id,
    )

    processed = strip_mention(user_message) or user_message

    if persona.id != "steve":
        await _stream_local(send_json, processed, session_id, persona_id=persona.id, profile="local")
        return

    # Steve: dispatch to the resolved coding engine.
    if engine.id == "opencode":
        if engine.available:
            await _stream_opencode(send_json, processed, session_id)
            return
        await send_json(
            {
                "type": "token",
                "data": (
                    "\n\n*OpenCode engine is unavailable "
                    f"({engine.detail}); falling back to the local model.*\n\n"
                ),
                "session_id": session_id,
            }
        )
        await _stream_local(send_json, processed, session_id, persona_id="steve", profile="local")
        return

    profile = "steve" if engine.id == "deepseek" else "local"
    await _stream_local(send_json, processed, session_id, persona_id="steve", profile=profile)


async def _stream_local(
    send_json,
    user_message: str,
    session_id: str,
    *,
    persona_id: str = DEFAULT_PERSONA_ID,
    profile: str = "local",
):
    token_stream = await run_agent_turn(
        user_message,
        session_id,
        stream=True,
        persona_id=persona_id,
        profile=profile,
    )
    async for token in token_stream:
        await send_json(
            {"type": "token", "data": token, "session_id": session_id}
        )
    await send_json({"type": "done", "session_id": session_id})


async def _stream_opencode(send_json, user_message: str, session_id: str):
    if user_message.strip().lower() == "/help":
        await _stream_local(send_json, user_message, session_id, persona_id="steve")
        return

    cwd = str(mount_root()) if mount_active() else None
    async for chunk in run_opencode(
        user_message,
        session_id,
        cwd=cwd,
        system_prompt=persona_system_prompt("steve"),
    ):
        await send_json(
            {"type": "token", "data": chunk, "session_id": session_id}
        )
    await send_json({"type": "done", "session_id": session_id})
