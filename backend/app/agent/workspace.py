from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from app.config import get_runtime
from app.auth.oidc import oidc

logger = logging.getLogger(__name__)

DEFAULT_MOUNT = "/workspace"
DEFAULT_OVERLAY = "/data/monofs-overlay"

SESSION_STATUS_TIMEOUT = 15.0
SESSION_QUICK_TIMEOUT = 30.0
SESSION_COMMIT_TIMEOUT = 120.0
SESSION_PUSH_TIMEOUT = 570.0  # CLI uses a 10 minute socket timeout for push
MAX_READ_BYTES = 120_000
MAX_LIST_ENTRIES = 500


class WorkspaceError(Exception):
    """Raised for sandbox/availability violations in workspace tools."""


def mount_root() -> Path:
    return Path(str(get_runtime("monofs_mount") or DEFAULT_MOUNT)).expanduser()


def overlay_dir() -> Path:
    return Path(str(get_runtime("monofs_overlay") or DEFAULT_OVERLAY)).expanduser()


def router_addr() -> str:
    return str(get_runtime("monofs_router") or "").strip()


def session_socket() -> Path:
    return overlay_dir() / "session.sock"


def mount_active() -> bool:
    """True when a MonoFS mount (or its session socket) is available."""
    root = mount_root()
    try:
        if os.path.ismount(root):
            return True
    except OSError:
        pass
    return session_socket().exists()


def workspace_snapshot() -> dict:
    """Cheap, side-effect-free status used by the settings/status APIs."""
    root = mount_root()
    return {
        "mount": str(root),
        "overlay": str(overlay_dir()),
        "router": router_addr(),
        "mounted": mount_active(),
        "socket": session_socket().exists(),
        "writable": mount_active(),
    }


def _is_confirmed(args: dict) -> bool:
    value = args.get("confirm", False)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "confirm", "approved"}


def _truncate(text: str, max_len: int = 8000) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "\n...[truncated]"


def _resolve_path(rel: str, *, default: str = ".") -> Path:
    """Resolve a workspace-relative path, refusing anything outside the mount root."""
    root = mount_root()
    root_resolved = root.resolve()

    cleaned = str(rel or default).strip() or default
    candidate = Path(cleaned)
    if not candidate.is_absolute():
        candidate = root_resolved / cleaned

    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise WorkspaceError(f"cannot resolve path '{cleaned}': {exc}") from exc

    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise WorkspaceError(
            f"path '{cleaned}' escapes the MonoFS workspace root ({root_resolved})"
        )
    return resolved


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(mount_root().resolve()))
    except ValueError:
        return str(path)


async def _run_session(args: list[str], timeout: float = SESSION_QUICK_TIMEOUT) -> str:
    """Run the monofs-session CLI against the configured overlay socket."""
    if not session_socket().exists():
        raise WorkspaceError(
            "MonoFS workspace is not active (no session socket at "
            f"{session_socket()}). Configure MONOFS_ROUTER and ensure monofs-client is running."
        )

    env = {**os.environ, "MONOFS_OVERLAY_DIR": str(overlay_dir())}
    token = await oidc.get_token()
    if token:
        env["MONOFS_TOKEN"] = token
    proc = await asyncio.create_subprocess_exec(
        "monofs-session",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise WorkspaceError(f"monofs-session {' '.join(args)} timed out after {timeout}s")
    out = stdout.decode("utf-8", errors="replace").strip()
    err = stderr.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        detail = "\n".join(part for part in (err, out) if part)
        return f"Error (exit {proc.returncode}):\n{detail}"
    return out or "(empty output)"


# ---------------------------------------------------------------------------
# Tool handlers (registered in tools.py)
# ---------------------------------------------------------------------------

async def workspace_status(args: dict) -> str:
    snapshot = workspace_snapshot()
    lines = [
        f"Mount: {snapshot['mount']}",
        f"Overlay: {snapshot['overlay']}",
        f"Router: {snapshot['router'] or '(not configured)'}",
        f"Active: {'yes' if snapshot['mounted'] else 'no'}",
    ]
    if not snapshot["socket"]:
        lines.append(
            "Session socket not found. Steve cannot edit/commit/push until the MonoFS "
            "mount is running (set MONOFS_ROUTER and restart the backend)."
        )
        return "\n".join(lines)
    try:
        lines.append("")
        lines.append(await _run_session(["status"], timeout=SESSION_STATUS_TIMEOUT))
    except WorkspaceError as exc:
        lines.append(str(exc))
    return _truncate("\n".join(lines))


async def workspace_list(args: dict) -> str:
    try:
        target = _resolve_path(str(args.get("path", ".")))
    except WorkspaceError as exc:
        return f"Error: {exc}"
    if not target.is_dir():
        return f"Error: '{_relative(target)}' is not a directory"

    entries: list[str] = []
    try:
        for entry in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if len(entries) >= MAX_LIST_ENTRIES:
                entries.append("...[truncated]")
                break
            suffix = "/" if entry.is_dir() else ""
            size = ""
            if entry.is_file():
                try:
                    size = f"  ({entry.stat().st_size} bytes)"
                except OSError:
                    size = ""
            entries.append(f"{entry.name}{suffix}{size}")
    except OSError as exc:
        return f"Error listing '{_relative(target)}': {exc}"

    if not entries:
        return f"'{_relative(target)}' is empty"
    return _truncate("\n".join(entries))


async def workspace_read(args: dict) -> str:
    raw = str(args.get("path", "")).strip()
    if not raw:
        return "Error: path is required"
    try:
        target = _resolve_path(raw)
    except WorkspaceError as exc:
        return f"Error: {exc}"
    if not target.is_file():
        return f"Error: '{_relative(target)}' is not a file"
    try:
        data = target.read_bytes()
    except OSError as exc:
        return f"Error reading '{_relative(target)}': {exc}"

    truncated = len(data) > MAX_READ_BYTES
    chunk = data[:MAX_READ_BYTES]
    try:
        text = chunk.decode("utf-8")
    except UnicodeDecodeError:
        return (
            f"'{_relative(target)}' appears to be binary ({len(data)} bytes). "
            "Reading binary files is not supported."
        )
    if truncated:
        text += f"\n...[truncated at {MAX_READ_BYTES} bytes of {len(data)}]"
    return text


async def workspace_write(args: dict) -> str:
    raw = str(args.get("path", "")).strip()
    content = args.get("content")
    if not raw:
        return "Error: path is required"
    if content is None:
        return "Error: content is required"
    if not isinstance(content, str):
        content = str(content)

    try:
        target = _resolve_path(raw)
    except WorkspaceError as exc:
        return f"Error: {exc}"

    create_dirs = str(args.get("create_dirs", "true")).strip().lower() not in {"false", "0", "no"}
    existed = target.exists()
    try:
        if create_dirs:
            target.parent.mkdir(parents=True, exist_ok=True)
        elif not target.parent.is_dir():
            return f"Error: parent directory of '{_relative(target)}' does not exist"
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"Error writing '{_relative(target)}': {exc}"

    state = "updated" if existed else "created"
    return f"Wrote {len(content.encode('utf-8'))} bytes to {_relative(target)} ({state})"


async def workspace_diff(args: dict) -> str:
    cmd = ["diff"]
    path = str(args.get("path", "")).strip()
    if path:
        try:
            path = _relative(_resolve_path(path))
        except WorkspaceError as exc:
            return f"Error: {exc}"
        cmd.append(path)
    try:
        return _truncate(await _run_session(cmd, timeout=SESSION_QUICK_TIMEOUT))
    except WorkspaceError as exc:
        return f"Error: {exc}"


_SESSION_ACTIONS = {
    "start": ("start", []),
    "add": ("add", ["paths"]),
    "rm": ("rm", ["paths"]),
    "discard": ("discard", []),
    "log": ("log", []),
    "refs": ("refs", []),
    "status": ("status", []),
    "branch_show": ("branch", ["show"]),
    "branch_create": ("branch", ["create", "name"]),
    "branch_switch": ("branch", ["switch", "name"]),
}


async def workspace_session(args: dict) -> str:
    action = str(args.get("action", "")).strip().lower()
    if action not in _SESSION_ACTIONS:
        return f"Error: action must be one of: {', '.join(sorted(_SESSION_ACTIONS))}"
    if action == "discard" and not _is_confirmed(args):
        return (
            "Error: 'discard' destroys all local workspace changes and is irreversible.\n"
            "Ask the user for approval, then re-run with confirm=true."
        )

    subcommand, required = _SESSION_ACTIONS[action]
    cmd = [subcommand]
    for field in required:
        value = str(args.get(field, "")).strip()
        if not value:
            return f"Error: '{field}' is required for action '{action}'"
        if field == "paths":
            paths = [p.strip() for p in value.split(",") if p.strip()]
            if not paths:
                return f"Error: '{field}' is required for action '{action}'"
            cmd.extend(paths)
        else:
            cmd.append(value)
    try:
        return _truncate(await _run_session(cmd, timeout=SESSION_QUICK_TIMEOUT))
    except WorkspaceError as exc:
        return f"Error: {exc}"


async def workspace_commit(args: dict) -> str:
    message = str(args.get("message", "")).strip()
    if not message:
        return "Error: message is required"

    try:
        lines: list[str] = []
        paths_raw = str(args.get("paths", "")).strip()
        if paths_raw:
            paths = [p.strip() for p in paths_raw.split(",") if p.strip()]
            lines.append(await _run_session(["add", *paths], timeout=SESSION_QUICK_TIMEOUT))
            lines.append("")
        lines.append(await _run_session(["commit", "--message", message], timeout=SESSION_COMMIT_TIMEOUT))
        lines.append("")
        lines.append(await _run_session(["status"], timeout=SESSION_STATUS_TIMEOUT))
        return _truncate("\n".join(lines))
    except WorkspaceError as exc:
        return f"Error: {exc}"


async def workspace_push(args: dict) -> str:
    try:
        result = await _run_session(["push"], timeout=SESSION_PUSH_TIMEOUT)
        refs = await _run_session(["refs"], timeout=SESSION_STATUS_TIMEOUT)
        return _truncate(f"{result}\n\n{refs}")
    except WorkspaceError as exc:
        return f"Error: {exc}"
