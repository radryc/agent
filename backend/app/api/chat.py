from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import signal

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.agent.loop import run_agent_turn, ensure_session
from app.agent.opencode import run_opencode
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
            agent = data.get("agent", "local")

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
                if agent == "opencode":
                    await _stream_opencode(safe_send, user_message, session_id)
                else:
                    await _stream_local(safe_send, user_message, session_id)
            except Exception as e:
                logger.exception("Error during agent turn")
                await safe_send(
                    {"type": "error", "data": str(e), "session_id": session_id}
                )

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    finally:
        await stop_terminal_streaming()


async def _stream_local(send_json, user_message: str, session_id: str):
    token_stream = await run_agent_turn(user_message, session_id, stream=True)
    async for token in token_stream:
        await send_json(
            {"type": "token", "data": token, "session_id": session_id}
        )
    await send_json({"type": "done", "session_id": session_id})


async def _stream_opencode(send_json, user_message: str, session_id: str):
    if user_message.strip().lower() == "/help":
        await _stream_local(send_json, user_message, session_id)
        return

    async for chunk in run_opencode(user_message, session_id):
        await send_json(
            {"type": "token", "data": chunk, "session_id": session_id}
        )
    await send_json({"type": "done", "session_id": session_id})
