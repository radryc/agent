from __future__ import annotations

import logging
import os
from typing import AsyncIterator

import httpx

from app.config import get_runtime

logger = logging.getLogger(__name__)

def _server_url() -> str:
    return str(get_runtime("opencode_server_url") or "").strip()


def _sdk_import_ok() -> bool:
    try:
        import opencode_agent_sdk  # noqa: F401
    except Exception:
        return False
    return True


class OpenCodeAgentService:
    def __init__(self) -> None:
        self._resume_by_session: dict[str, str] = {}

    async def is_available(self) -> bool:
        server_url = _server_url()
        if not server_url or not _sdk_import_ok():
            return False

        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                for path in ("/health", "/api/health", "/"):
                    resp = await client.get(f"{server_url.rstrip('/')}{path}")
                    if resp.status_code < 500:
                        return True
                return False
        except Exception:
            logger.exception("OpenCode SDK availability probe failed")
            return False

    async def stream(
        self,
        prompt: str,
        session_id: str | None = None,
        cwd: str | None = None,
    ) -> AsyncIterator[str]:
        server_url = _server_url()
        if not server_url:
            yield "\x1b[31mOpenCode server is not configured.\x1b[0m\r\n"
            yield "Set OpenCode Server URL in Settings to enable OpenCode agent.\r\n"
            return

        if not _sdk_import_ok():
            yield "\x1b[31mOpenCode SDK is not installed on backend.\x1b[0m\r\n"
            yield "Install dependency: \x1b[33mpip install opencode-agent-sdk\x1b[0m\r\n"
            return

        from opencode_agent_sdk import (
            AgentOptions,
            AssistantMessage,
            ResultMessage,
            SDKClient,
            TextBlock,
        )
        from opencode_agent_sdk._errors import ProcessError

        model = str(get_runtime("opencode_model") or "").strip()
        provider_id = str(get_runtime("opencode_provider_id") or "anthropic").strip()
        system_prompt = str(get_runtime("opencode_system_prompt") or "").strip()
        working_dir = cwd or os.getcwd()
        resume_id = self._resume_by_session.get(session_id or "")

        logger.info(
            "Running OpenCode via SDK: session=%s server_url=%s cwd=%s model=%s provider=%s resume=%s",
            session_id or "none",
            server_url,
            working_dir,
            model or "(default)",
            provider_id,
            resume_id or "none",
        )

        client = SDKClient(
            options=AgentOptions(
                cwd=working_dir,
                server_url=server_url,
                model=model,
                provider_id=provider_id,
                system_prompt=system_prompt,
                resume=resume_id,
            )
        )

        try:
            await client.connect()
            await client.query(prompt)

            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock) and block.text:
                            yield block.text
                elif isinstance(message, ResultMessage):
                    if session_id and message.session_id:
                        self._resume_by_session[session_id] = message.session_id
                    if message.is_error:
                        yield "\r\n\x1b[33mOpenCode returned an error result.\x1b[0m\r\n"
        except ProcessError as exc:
            logger.exception("OpenCode SDK process error")
            yield f"\x1b[31mOpenCode request failed: {exc}\x1b[0m\r\n"
        except Exception as exc:
            logger.exception("OpenCode SDK unexpected error")
            yield f"\x1b[31mOpenCode request failed: {exc}\x1b[0m\r\n"
        finally:
            try:
                await client.disconnect()
            except Exception:
                logger.debug("Ignoring OpenCode SDK disconnect error", exc_info=True)


_opencode_service = OpenCodeAgentService()


async def is_opencode_available() -> bool:
    return await _opencode_service.is_available()


async def run_opencode(
    prompt: str,
    session_id: str | None = None,
    cwd: str | None = None,
) -> AsyncIterator[str]:
    async for chunk in _opencode_service.stream(prompt, session_id=session_id, cwd=cwd):
        yield chunk
