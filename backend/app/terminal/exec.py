from __future__ import annotations

import asyncio
import contextlib
import errno
import fcntl
import logging
import os
import re
import signal
import struct
import termios
import time
from typing import AsyncIterator

from app.agent import session as session_mgr

logger = logging.getLogger(__name__)

_cwd_registry: dict[str, str] = {}


def _session_cwd(session_id: str) -> str:
    if session_id not in _cwd_registry:
        _cwd_registry[session_id] = os.getcwd()
    return _cwd_registry[session_id]


def _set_session_cwd(session_id: str, path: str):
    _cwd_registry[session_id] = path


_CD_RE = re.compile(r'^\s*cd\s+(.+)')


class TerminalSession:
    def __init__(self, session_id: str, pid: int, fd: int, shell: str):
        self.session_id = session_id
        self.pid = pid
        self.fd = fd
        self.shell = shell
        self.output_task: asyncio.Task | None = None
        self.subscribers: set[asyncio.Queue[dict]] = set()
        self.closed = False
        self.last_active = time.time()
        self.cwd = ""


class TerminalManager:
    def __init__(self):
        self._sessions: dict[str, TerminalSession] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._cleanup_task and not self._cleanup_task.done():
            return
        self._cleanup_task = asyncio.create_task(self._idle_cleanup_loop())

    async def stop(self) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
            self._cleanup_task = None

        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()

        for s in sessions:
            await self._shutdown_session(s)

    async def ensure_session(self, session_id: str, shell: str = "/bin/bash") -> TerminalSession:
        state = await session_mgr.get_terminal_state(session_id)
        start_cwd = state["cwd"] if state and state.get("cwd") else os.getcwd()
        start_cwd = os.path.realpath(os.path.expanduser(start_cwd))

        async with self._lock:
            existing = self._sessions.get(session_id)
            if existing and not existing.closed:
                existing.last_active = time.time()
                return existing

            pid, fd = await asyncio.to_thread(self._spawn_pty, shell, start_cwd)
            created = TerminalSession(session_id=session_id, pid=pid, fd=fd, shell=shell)
            self._sessions[session_id] = created
            created.output_task = asyncio.create_task(self._read_output(created))
            return created

    async def subscribe(self, session_id: str) -> asyncio.Queue[dict]:
        session = await self.ensure_session(session_id)
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=512)
        session.subscribers.add(q)
        session.last_active = time.time()
        await self._enqueue(q, {"type": "terminal_ready", "session_id": session_id})

        cwd = await self._read_session_cwd(session)
        if cwd:
            await self._enqueue(
                q,
                {
                    "type": "terminal_cwd",
                    "session_id": session_id,
                    "cwd": self._display_cwd(cwd),
                },
            )

        history = await session_mgr.list_terminal_history(session_id)
        await self._enqueue(
            q,
            {
                "type": "terminal_sync",
                "session_id": session_id,
                "history": [item["command"] for item in history],
                "cwd": self._display_cwd(cwd) if cwd else "",
            },
        )
        return q

    async def unsubscribe(self, session_id: str, queue: asyncio.Queue[dict]) -> None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return
            session.subscribers.discard(queue)
            session.last_active = time.time()

    async def write(self, session_id: str, data: str) -> None:
        session = await self.ensure_session(session_id)
        if session.closed:
            return

        encoded = data.encode("utf-8", errors="replace")
        await asyncio.to_thread(os.write, session.fd, encoded)
        session.last_active = time.time()

    async def resize(self, session_id: str, cols: int, rows: int) -> None:
        session = await self.ensure_session(session_id)
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        await asyncio.to_thread(fcntl.ioctl, session.fd, termios.TIOCSWINSZ, winsize)
        session.last_active = time.time()

    async def signal(self, session_id: str, sig: int) -> None:
        session = await self.ensure_session(session_id)
        if session.closed:
            return
        with contextlib.suppress(ProcessLookupError):
            os.kill(session.pid, sig)
        session.last_active = time.time()

    async def add_history(self, session_id: str, command: str) -> None:
        await session_mgr.add_terminal_history(session_id, command)

    async def _read_output(self, session: TerminalSession) -> None:
        try:
            while not session.closed:
                try:
                    chunk = await asyncio.to_thread(os.read, session.fd, 4096)
                    if not chunk:
                        break
                except OSError as exc:
                    if exc.errno in (errno.EIO, errno.EBADF):
                        break
                    raise

                session.last_active = time.time()
                data = chunk.decode("utf-8", errors="replace")
                await self._broadcast(
                    session,
                    {
                        "type": "terminal_output",
                        "session_id": session.session_id,
                        "data": data,
                    },
                )

                cwd = await self._read_session_cwd(session)
                if cwd and cwd != session.cwd:
                    session.cwd = cwd
                    await self._broadcast(
                        session,
                        {
                            "type": "terminal_cwd",
                            "session_id": session.session_id,
                            "cwd": self._display_cwd(cwd),
                        },
                    )
        except Exception as exc:
            logger.exception("Terminal output loop failed for session %s", session.session_id)
            await self._broadcast(
                session,
                {
                    "type": "terminal_error",
                    "session_id": session.session_id,
                    "data": str(exc),
                },
            )
        finally:
            await self._close_session(session)

    async def _close_session(self, session: TerminalSession) -> None:
        if session.closed:
            return
        session.closed = True

        exit_code = await asyncio.to_thread(self._wait_pid, session.pid)
        cwd = await self._read_session_cwd(session) or session.cwd
        if cwd:
            await session_mgr.upsert_terminal_state(session.session_id, cwd, shell=session.shell)

        await self._broadcast(
            session,
            {
                "type": "terminal_exit",
                "session_id": session.session_id,
                "exit_code": exit_code,
            },
        )

        with contextlib.suppress(OSError):
            os.close(session.fd)

        async with self._lock:
            current = self._sessions.get(session.session_id)
            if current is session:
                del self._sessions[session.session_id]

    async def _shutdown_session(self, session: TerminalSession) -> None:
        if not session.closed:
            with contextlib.suppress(ProcessLookupError):
                os.kill(session.pid, signal.SIGTERM)
        if session.output_task and not session.output_task.done():
            session.output_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session.output_task
        await self._close_session(session)

    async def _idle_cleanup_loop(self) -> None:
        timeout_seconds = 30 * 60
        while True:
            await asyncio.sleep(30)
            now = time.time()
            stale: list[TerminalSession] = []
            async with self._lock:
                for session in self._sessions.values():
                    if session.subscribers:
                        continue
                    if now - session.last_active > timeout_seconds:
                        stale.append(session)

            for session in stale:
                await self._shutdown_session(session)

    async def _broadcast(self, session: TerminalSession, msg: dict) -> None:
        if not session.subscribers:
            return
        dead: list[asyncio.Queue[dict]] = []
        for q in session.subscribers:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            session.subscribers.discard(q)

    async def _enqueue(self, queue: asyncio.Queue[dict], msg: dict) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(msg)

    def _spawn_pty(self, shell: str, start_cwd: str) -> tuple[int, int]:
        pid, fd = os.forkpty()
        if pid == 0:
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            env["COLORTERM"] = "truecolor"
            env["SHELL"] = shell
            with contextlib.suppress(Exception):
                os.chdir(start_cwd)
            try:
                os.execvpe(shell, [shell, "-i"], env)
            except Exception:
                os._exit(127)

        return pid, fd

    async def _read_session_cwd(self, session: TerminalSession) -> str:
        proc_cwd = f"/proc/{session.pid}/cwd"
        try:
            cwd = await asyncio.to_thread(os.readlink, proc_cwd)
        except OSError:
            return ""
        return cwd

    def _display_cwd(self, cwd: str) -> str:
        home = os.path.expanduser("~")
        if cwd.startswith(home):
            return "~" + cwd[len(home):]
        return cwd

    def _wait_pid(self, pid: int) -> int:
        try:
            _, status = os.waitpid(pid, 0)
        except ChildProcessError:
            return 0
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        if os.WIFSIGNALED(status):
            return 128 + os.WTERMSIG(status)
        return 0

_terminal_manager = TerminalManager()


def get_terminal_manager() -> TerminalManager:
    return _terminal_manager


async def execute_command(
    command: str,
    session_id: str,
) -> AsyncIterator[dict]:
    cmd = command.strip()

    cd_match = _CD_RE.match(cmd)
    if cd_match:
        target = cd_match.group(1).strip().strip('"').strip("'")
        current = _session_cwd(session_id)
        try:
            new_path = os.path.join(current, target)
            new_path = os.path.realpath(os.path.expanduser(new_path))
            if os.path.isdir(new_path):
                _set_session_cwd(session_id, new_path)
                home = os.path.expanduser("~")
                display = new_path
                if display.startswith(home):
                    display = "~" + display[len(home):]
                yield {"type": "exec_cwd", "cwd": display}
                yield {"type": "exec_done", "exit_code": 0}
            else:
                yield {"type": "exec_output", "data": f"cd: {target}: No such directory\n"}
                yield {"type": "exec_done", "exit_code": 1}
        except Exception as exc:
            yield {"type": "exec_output", "data": f"cd: {exc}\n"}
            yield {"type": "exec_done", "exit_code": 1}
        return

    cwd = _session_cwd(session_id)

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            executable="/bin/bash",
        )
    except Exception as exc:
        logger.exception("Failed to spawn command: %s", cmd)
        yield {"type": "exec_output", "data": f"Error: {exc}\n"}
        yield {"type": "exec_done", "exit_code": 1}
        return

    queue: asyncio.Queue[dict] = asyncio.Queue()

    async def reader(stream, kind: str):
        while True:
            line = await stream.readline()
            if not line:
                break
            await queue.put(
                {"type": "exec_output", "data": line.decode(errors="replace")}
            )

    tasks = []
    if proc.stdout:
        tasks.append(asyncio.create_task(reader(proc.stdout, "stdout")))
    if proc.stderr:
        tasks.append(asyncio.create_task(reader(proc.stderr, "stderr")))

    finished = 0
    while finished < len(tasks):
        try:
            msg = await asyncio.wait_for(queue.get(), timeout=0.1)
            yield msg
        except asyncio.TimeoutError:
            pass
        finished = sum(1 for t in tasks if t.done())

    await proc.wait()

    home = os.path.expanduser("~")
    display = cwd
    if display.startswith(home):
        display = "~" + display[len(home):]

    yield {"type": "exec_cwd", "cwd": display}
    yield {
        "type": "exec_done",
        "exit_code": proc.returncode or 0,
    }
