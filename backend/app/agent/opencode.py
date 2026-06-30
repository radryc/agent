from __future__ import annotations

import asyncio
import logging
import os
import shutil
from typing import AsyncIterator

from app.config import get_runtime

logger = logging.getLogger(__name__)

OPENCODE_BIN = "opencode"


def _find_opencode() -> str | None:
    """Locate the opencode binary on PATH."""
    path = shutil.which(OPENCODE_BIN)
    if path:
        return path

    # Check common install locations
    candidates = [
        os.path.expanduser("~/.local/bin/opencode"),
        os.path.expanduser("~/.cargo/bin/opencode"),
        "/usr/local/bin/opencode",
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


async def is_opencode_available() -> bool:
    return _find_opencode() is not None


async def run_opencode(
    prompt: str,
    session_id: str | None = None,
    cwd: str | None = None,
) -> AsyncIterator[str]:
    """
    Run opencode with the given prompt and stream the output.

    Uses the opencode CLI in pipe mode: writes the prompt to stdin,
    then yields stdout lines as they arrive.

    If opencode is not available, yields a helpful error message.
    """
    bin_path = _find_opencode()

    if not bin_path:
        yield "\x1b[31mOpenCode is not installed.\x1b[0m\r\n"
        yield "\r\nRun: \x1b[33mcurl -fsSL https://opencode.ai/install | bash\x1b[0m\r\n"
        return

    working_dir = cwd or os.getcwd()

    logger.info(
        "Running opencode: bin=%s prompt_len=%d session=%s cwd=%s",
        bin_path,
        len(prompt),
        session_id or "none",
        working_dir,
    )

    # Use 'opencode' in non-interactive mode.
    # The CLI accepts piped input: echo "prompt" | opencode
    #
    # Strategy: pass -p flag for prompt, or write to stdin.
    # We'll try both approaches.

    # Build environment with API key if configured
    env = os.environ.copy()
    opencode_key = get_runtime("opencode_api_key")
    if opencode_key and opencode_key.strip():
        env["OPENAI_API_KEY"] = opencode_key.strip()
        logger.info("OpenCode API key configured (len=%d)", len(opencode_key.strip()))

    try:
        # Approach: use opencode with -p flag (non-interactive prompt mode)
        # Note: opencode may or may not support -p; fallback to stdin pipe
        proc = await asyncio.create_subprocess_exec(
            bin_path,
            "-p",
            prompt,
            "--print",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir,
            env=env,
        )
    except Exception:
        # Fallback: try without --print flag
        try:
            proc = await asyncio.create_subprocess_exec(
                bin_path,
                "-p",
                prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_dir,
                env=env,
            )
        except Exception as exc:
            logger.exception("Failed to spawn opencode")
            yield f"\x1b[31mFailed to start opencode: {exc}\x1b[0m\r\n"
            return

    async def read_stderr():
        if proc.stderr:
            async for line in proc.stderr:
                decoded = line.decode(errors="replace")
                logger.debug("opencode stderr: %s", decoded.strip())

    stderr_task = asyncio.ensure_future(read_stderr())

    try:
        if proc.stdout:
            async for line in proc.stdout:
                decoded = line.decode(errors="replace")
                yield decoded
    except Exception as exc:
        logger.exception("Error reading opencode stdout")
        yield f"\r\n\x1b[31mError: {exc}\x1b[0m\r\n"
    finally:
        await proc.wait()
        if not stderr_task.done():
            stderr_task.cancel()
            try:
                await stderr_task
            except asyncio.CancelledError:
                pass

    if proc.returncode != 0:
        yield f"\r\n\x1b[33mOpenCode exited with code {proc.returncode}\x1b[0m\r\n"
