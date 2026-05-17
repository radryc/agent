from __future__ import annotations

import json
import logging
import re
from typing import AsyncIterator

from app.config import settings, get_runtime
from app.agent import session as session_mgr
from app.agent.tools import execute_tool
from app.llm.client import chat_completion

logger = logging.getLogger(__name__)

# Matches a bare JSON tool-call line: {"tool": "...", "args": {...}}
_TOOL_RE = re.compile(r'^\s*\{"tool"\s*:\s*".+?".*\}\s*$', re.MULTILINE)

MAX_TOOL_ROUNDS = 5

# Always appended to the system prompt so tools work even with custom prompts
_TOOL_INSTRUCTIONS = (
    "\n\nYou have access to the following tools. To use a tool, output ONLY a JSON block "
    "on its own line in this exact format:\n"
    '{"tool": "<name>", "args": {<params>}}\n\n'
    "Available tools:\n"
    "- web_search(query, max_results=5): Search the web for current information. "
    "Use this whenever the user asks about recent events, news, weather, prices, or anything you're not sure about.\n"
    "- datetime_now(): Get the current date and time in UTC.\n"
    "- calculate(expression): Evaluate a mathematical expression.\n"
    "- generate_image(prompt, loras=''): Generate an image using wan2gp Z-Image. "
    "Returns a viewable image URL. Specify any lora names in the loras field.\n\n"
    "After receiving a tool result, use it to formulate your final answer. "
    "Do NOT output tool calls in your final answer."
)


def _extract_tool_call(text: str) -> dict | None:
    """Return parsed tool call dict if the model output contains one, else None."""
    match = _TOOL_RE.search(text)
    if not match:
        return None
    try:
        obj = json.loads(match.group())
        if "tool" in obj:
            return obj
    except json.JSONDecodeError:
        pass
    return None


async def run_agent_turn(
    user_message: str,
    session_id: str,
    *,
    stream: bool = True,
) -> AsyncIterator[str] | str:
    history = await session_mgr.get_messages(session_id)

    system_prompt = get_runtime("system_prompt") or settings.agent_system_prompt
    # Always inject tool instructions, even if user customized the system prompt
    full_system_prompt = system_prompt + _TOOL_INSTRUCTIONS
    logger.info("[LOOP] Starting turn. System prompt (first 200): %.200s", system_prompt)
    messages = [{"role": "system", "content": full_system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    await session_mgr.add_message(session_id, "user", user_message)

    if not stream:
        result = await _run_tool_loop(messages)
        await session_mgr.add_message(session_id, "assistant", result)
        return result

    return _stream_tool_loop(messages, session_id)


async def _run_tool_loop(messages: list[dict]) -> str:
    """Non-streaming: run up to MAX_TOOL_ROUNDS of tool calls then return final answer."""
    for _ in range(MAX_TOOL_ROUNDS):
        response = await chat_completion(messages, stream=False)
        logger.info("[LOOP] LLM raw response (non-stream): %.500s", response)
        tool_call = _extract_tool_call(response)
        if not tool_call:
            logger.info("[LOOP] No tool call detected, returning response.")
            return response

        tool_name = tool_call.get("tool", "")
        tool_args = tool_call.get("args", {})
        logger.info("Tool call: %s %s", tool_name, tool_args)

        tool_result = await execute_tool(tool_name, tool_args)
        logger.info("Tool result: %.200s", tool_result)

        # Feed result back as a user message so the model can use it
        messages.append({"role": "assistant", "content": response})
        messages.append({
            "role": "user",
            "content": f"[Tool result for {tool_name}]\n{tool_result}\n\nNow answer based on this."
        })

    # Fallback: final completion after max rounds
    return await chat_completion(messages, stream=False)


async def _stream_tool_loop(
    messages: list[dict], session_id: str
) -> AsyncIterator[str]:
    """Streaming: detect tool calls, execute silently, then stream the final answer."""
    working_messages = list(messages)

    for round_num in range(MAX_TOOL_ROUNDS):
        # Collect full response first to check for tool calls
        collected: list[str] = []
        token_stream = await chat_completion(working_messages, stream=True)
        async for token in token_stream:
            collected.append(token)
        response = "".join(collected)
        logger.info("[LOOP] LLM raw response (stream round %d): %.500s", round_num, response)

        tool_call = _extract_tool_call(response)
        if not tool_call:
            logger.info("[LOOP] No tool call detected in round %d, streaming final answer.", round_num)
            # No tool call — stream the final answer token by token
            for token in collected:
                yield token
            if response:
                await session_mgr.add_message(session_id, "assistant", response)
            return

        # Execute tool silently (no streaming to client yet)
        tool_name = tool_call.get("tool", "")
        tool_args = tool_call.get("args", {})
        logger.info("Tool call (round %d): %s %s", round_num, tool_name, tool_args)

        # Yield a status indicator so the UI shows something is happening
        yield f"\n\n*Searching: {tool_args.get('query', tool_name)}…*\n\n"

        tool_result = await execute_tool(tool_name, tool_args)
        logger.info("Tool result: %.200s", tool_result)

        working_messages.append({"role": "assistant", "content": response})
        working_messages.append({
            "role": "user",
            "content": f"[Tool result for {tool_name}]\n{tool_result}\n\nNow answer based on this."
        })

    # Max rounds exhausted — do one final non-streaming call
    final = await chat_completion(working_messages, stream=False)
    yield final
    if final:
        await session_mgr.add_message(session_id, "assistant", final)


async def ensure_session(session_id: str | None) -> str:
    if session_id:
        existing = await session_mgr.get_session(session_id)
        if existing:
            return session_id

    result = await session_mgr.create_session()
    return result["id"]
