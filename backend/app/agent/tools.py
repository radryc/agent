from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import ipaddress
import os
import shutil
import socket
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Awaitable
from urllib.parse import quote_plus, urlparse

from duckduckgo_search import DDGS
import httpx

from app.config import get_runtime, runtime_bool, settings
from app.agent import workspace as workspace_tools
from app.auth.oidc import oidc

logger = logging.getLogger(__name__)

_DDG_BACKENDS = ("auto", "lite", "html")
_DDG_MAX_RESULTS = 10
_DDG_ATTEMPTS_PER_BACKEND = 2
_DDG_RETRY_BASE_SECONDS = 1.0

DOCTOR_QUERY_URL = settings.doctor_query_url
DOCTOR_TIMEOUT = httpx.Timeout(15.0)
GUARDIAN_TIMEOUT = httpx.Timeout(15.0)
MONOFS_SEARCH_TIMEOUT = 15.0
MAX_EVENT_ANALYSIS_LIMIT = 500


# ---------------------------------------------------------------------------
# Skill registry
# ---------------------------------------------------------------------------

@dataclass
class Skill:
    name: str
    category: str
    description: str
    parameters: dict[str, str]
    status_prefix: str | None = None
    mutating: bool = False
    # When set, the boolean runtime setting with this key can waive the
    # confirm=true mutation gate (e.g. autonomous push).
    auto_confirm_key: str | None = None
    handler: Callable[[dict], Awaitable[str]] | None = None


_skills: dict[str, Skill] = {}


def register(skill: Skill) -> Skill:
    _skills[skill.name] = skill
    return skill


def skill_list() -> list[Skill]:
    return list(_skills.values())


def get_skill(name: str) -> Skill | None:
    return _skills.get(name)


def build_tool_instructions(allowed: set[str] | None = None) -> str:
    cats: dict[str, list[Skill]] = {}
    for s in _skills.values():
        if allowed is not None and s.name not in allowed:
            continue
        cats.setdefault(s.category, []).append(s)

    lines = [
        "\n\nYou have access to infrastructure and diagnostic tools. "
        "To use a tool, output ONLY JSON blocks, one per line:\n"
        '{"tool": "<name>", "args": {<params>}}\n'
        "You may emit several independent calls in the same response; every "
        "line will be executed before you answer.\n",
        "\nEvidence rules (non-negotiable):\n"
        "- For any question about logs, traces, metrics, partitions, intents, "
        "assets, deployments, errors, latency, or health, call the relevant "
        "tool(s) before answering.\n"
        "- Answer only from tool results. Never invent counts, service names, "
        "timestamps, latencies, log lines, trace IDs, or query results.\n"
        "- If a tool returns an error, 401/403, or an empty result, say exactly "
        "that. Do not fill the gap with plausible-sounding telemetry.\n"
        "- If the user asks for logs AND traces (or metrics), query each signal "
        "separately. Do not skip traces just because logs came back empty; an "
        "empty trace result is itself evidence.\n"
        "- The persona controls tone, not facts.\n",
    ]

    order = ["general", "strata", "guardian", "doctor", "monofs", "workspace"]
    for cat_name in order:
        skills = cats.pop(cat_name, None)
        if not skills:
            continue
        lines.append(f"\n## {cat_name.upper()} TOOLS")
        for s in skills:
            params = ", ".join(f"{k}: {v}" for k, v in s.parameters.items()) if s.parameters else ""
            mutating_label = " [MUTATES STATE]" if s.mutating else ""
            lines.append(f"- {s.name}({params}): {s.description}{mutating_label}")

    for cat_name, skills in cats.items():
        lines.append(f"\n## {cat_name.upper()} TOOLS")
        for s in skills:
            params = ", ".join(f"{k}: {v}" for k, v in s.parameters.items()) if s.parameters else ""
            mutating_label = " [MUTATES STATE]" if s.mutating else ""
            lines.append(f"- {s.name}({params}): {s.description}{mutating_label}")

    lines.append(
        "\nImportant: After receiving tool results, use them to formulate your "
        "final answer. Do NOT output tool calls in your final answer. If the "
        "results are empty, say that; do not invent data to fill the response."
    )
    return "\n".join(lines)


# Re-export for backward compat
TOOLS = {}
_TOOL_INSTRUCTIONS = ""


def _rebuild_exports():
    global TOOLS, _TOOL_INSTRUCTIONS
    TOOLS = {
        name: {"description": s.description, "parameters": s.parameters}
        for name, s in _skills.items()
    }
    _TOOL_INSTRUCTIONS = build_tool_instructions()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAFE_MATH = {
    "abs": abs, "round": round, "min": min, "max": max, "sum": sum,
    "pow": pow, "sqrt": math.sqrt, "pi": math.pi, "e": math.e,
    "log": math.log, "log10": math.log10, "sin": math.sin, "cos": math.cos, "tan": math.tan,
}

_SCRIPT_STYLE_RE = re.compile(r"<\s*(script|style)[^>]*>.*?<\s*/\s*\1\s*>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


async def _guardian_get(path: str) -> str:
    raw_path = path.lstrip("/")
    candidates = [raw_path]
    if raw_path.startswith("api/") and not raw_path.startswith("api/m2m/"):
        candidates = ["api/m2m/" + raw_path[len("api/"):], raw_path]
    headers = await oidc.auth_headers()
    async with httpx.AsyncClient(timeout=GUARDIAN_TIMEOUT, headers=headers) as client:
        last_error: Exception | None = None
        for candidate in candidates:
            url = f"{settings.guardian_url.rstrip('/')}/{candidate}"
            try:
                r = await client.get(url)
                if r.status_code == 404 and candidate != candidates[-1]:
                    continue
                r.raise_for_status()
                return r.text
            except Exception as exc:  # pragma: no cover - preserves prior behavior
                last_error = exc
                if candidate == candidates[-1]:
                    raise
        if last_error is not None:
            raise last_error
        raise RuntimeError("guardian request failed")


async def _guardian_post(path: str) -> str:
    url = f"{settings.guardian_url.rstrip('/')}/{path.lstrip('/')}"
    headers = await oidc.auth_headers()
    async with httpx.AsyncClient(timeout=GUARDIAN_TIMEOUT, headers=headers) as client:
        r = await client.post(url)
        r.raise_for_status()
        return r.text


async def _doctor_get(path: str) -> str:
    url = f"{DOCTOR_QUERY_URL.rstrip('/')}/{path.lstrip('/')}"
    headers = await oidc.auth_headers()
    async with httpx.AsyncClient(timeout=DOCTOR_TIMEOUT, headers=headers) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.text


async def _run_subprocess(
    args: list[str],
    timeout: float = 10.0,
    *,
    env: dict[str, str] | None = None,
) -> str:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return f"Error: command timed out after {timeout}s"
    out = stdout.decode("utf-8", errors="replace").strip()
    err = stderr.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        detail = "\n".join(part for part in (err, out) if part)
        return f"Error (exit {proc.returncode}):\n{detail}"
    return out or "(empty output)"


async def _monofs_env() -> dict[str, str]:
    """Environment for monofs-session commands, including the current OIDC token."""
    env = os.environ.copy()
    token = await oidc.get_token()
    if token:
        env["MONOFS_TOKEN"] = token
        env["GUARDIAN_MONOFS_TOKEN"] = token
    return env


def _truncate(text: str, max_len: int = 8000) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "\n...[truncated]"


def _to_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _to_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _is_mutation_confirmed(args: dict) -> bool:
    return _to_bool(args.get("confirm"), default=False) or str(args.get("confirm", "")).strip().lower() in {
        "confirm",
        "approved",
        "yes",
    }


def _deep_get(mapping: dict, *keys: str) -> str:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            value = mapping[key]
            if isinstance(value, (str, int, float, bool)):
                return str(value)
    return ""


def _extract_event_list(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [entry for entry in payload if isinstance(entry, dict)]
    if not isinstance(payload, dict):
        return []

    queue: list[object] = [payload]
    candidates: list[list[dict]] = []
    while queue:
        node = queue.pop(0)
        if isinstance(node, list):
            dict_entries = [entry for entry in node if isinstance(entry, dict)]
            if dict_entries:
                candidates.append(dict_entries)
            continue
        if not isinstance(node, dict):
            continue
        for value in node.values():
            if isinstance(value, (dict, list)):
                queue.append(value)
    if not candidates:
        return []
    return max(candidates, key=len)


def _classify_event(level_text: str) -> str:
    normalized = level_text.lower()
    if any(token in normalized for token in ("error", "fail", "panic", "timeout", "crash", "degrad", "drift", "backoff")):
        return "failure"
    if any(token in normalized for token in ("success", "healthy", "reconcil", "ready", "applied", "complete")):
        return "success"
    return "unknown"


def _summarize_guardian_events(events: list[dict], *, requested_partition: str | None = None) -> str:
    if not events:
        return "No Guardian events were returned by Doctor for the requested filter."

    counts = Counter()
    by_partition: dict[str, Counter] = defaultdict(Counter)
    recent_failures: list[tuple[str, str, str, str]] = []

    for event in events:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        partition = (
            _deep_get(event, "partition", "partitionName", "partition_name")
            or _deep_get(metadata, "partition", "partitionName", "partition_name")
            or "unknown"
        )
        timestamp = _deep_get(event, "timestamp", "time", "createdAt", "eventTime") or "unknown-time"
        message = (
            _deep_get(event, "message", "reason", "summary", "title", "detail")
            or _deep_get(metadata, "message", "reason")
            or "(no message)"
        )
        level_text = " ".join(
            filter(
                None,
                [
                    _deep_get(event, "level", "severity", "status", "type", "state", "result"),
                    message,
                ],
            )
        )
        outcome = _classify_event(level_text)
        counts[outcome] += 1
        by_partition[partition][outcome] += 1
        if outcome == "failure":
            intent = _deep_get(event, "intent", "intentName", "intent_name") or _deep_get(metadata, "intent")
            recent_failures.append((timestamp, partition, intent or "-", message))

    partition_lines = []
    sorted_partitions = sorted(
        by_partition.items(),
        key=lambda item: (item[1]["failure"], item[1]["unknown"], item[1]["success"]),
        reverse=True,
    )
    for partition, partition_counts in sorted_partitions[:8]:
        partition_lines.append(
            f"- {partition}: failure={partition_counts['failure']}, success={partition_counts['success']}, unknown={partition_counts['unknown']}"
        )

    failure_lines: list[str] = []
    for timestamp, partition, intent, message in sorted(recent_failures, key=lambda item: item[0], reverse=True)[:5]:
        failure_lines.append(f"- {timestamp} | {partition} | intent={intent} | {message}")

    scope = f" for partition `{requested_partition}`" if requested_partition else ""
    lines = [
        f"Analyzed {len(events)} Guardian events from Doctor{scope}.",
        f"Outcome counts: failure={counts['failure']}, success={counts['success']}, unknown={counts['unknown']}.",
    ]
    if partition_lines:
        lines.append("Partition breakdown:")
        lines.extend(partition_lines)
    if failure_lines:
        lines.append("Recent failure signals:")
        lines.extend(failure_lines)
    else:
        lines.append("No failure events found in this sample.")
    lines.append("Recommended next steps: inspect unhealthy intents in Guardian, then correlate with Doctor logs/traces for the same timestamp window.")
    return "\n".join(lines)


def _guardianctl_bin() -> str | None:
    override = os.environ.get("GUARDIANCTL_BIN", "").strip()
    candidates = [override, "guardianctl", "/app/guardianctl", "./guardianctl"]
    for candidate in candidates:
        if not candidate:
            continue
        if candidate.startswith("/") or candidate.startswith("."):
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
            continue
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _guardianctl_base_command() -> list[str] | None:
    binary = _guardianctl_bin()
    if not binary:
        return None
    cmd = [binary, "-format", "json"]
    router = os.environ.get("MONOFS_ROUTER", "").strip() or str(get_runtime("monofs_router") or "").strip()
    if router:
        # Direct MonoFS access avoids Guardian UI discovery (which may be
        # loopback-only or protected by a separate discovery token).
        cmd.extend(["-monofs-router", router])
        return cmd

    guardian_url = str(settings.guardian_url or "").strip()
    if guardian_url:
        cmd.extend(["-guardian-url", guardian_url])
    discovery_token = os.environ.get("GUARDIAN_DISCOVERY_TOKEN", "").strip()
    if discovery_token:
        cmd.extend(["-guardian-discovery-token", discovery_token])
    return cmd


# ---------------------------------------------------------------------------
# URL validation helpers (for web_fetch)
# ---------------------------------------------------------------------------

async def _validate_public_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except Exception:
        return "Error: invalid url"
    if parsed.scheme not in {"http", "https"}:
        return "Error: url must start with http:// or https://"
    if not parsed.hostname:
        return "Error: url must include a hostname"
    hostname = parsed.hostname.strip().lower()
    if hostname in {"localhost", "localhost.localdomain"}:
        return "Error: local addresses are not allowed"
    try:
        ip = ipaddress.ip_address(hostname)
        addresses = [ip]
    except ValueError:
        try:
            loop = asyncio.get_running_loop()
            infos = await loop.getaddrinfo(
                hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror:
            return "Error: could not resolve hostname"
        except Exception as e:
            return f"Error: hostname resolution failed: {e}"
        addresses = []
        for info in infos:
            sockaddr = info[4]
            if sockaddr:
                try:
                    addresses.append(ipaddress.ip_address(sockaddr[0]))
                except ValueError:
                    continue
    for ip in addresses:
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return f"Error: fetching non-public addresses is not allowed ({ip})"
    return None


def _html_to_text(html: str) -> str:
    text = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", text)
    text = text.replace("&nbsp;", " ")
    return _WS_RE.sub(" ", text).strip()


def _extract_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    title = _TAG_RE.sub(" ", match.group(1))
    return _WS_RE.sub(" ", title).strip()


def _is_ddg_rate_limited(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(token in text for token in ("ratelimit", "rate limit", "429", "too many requests"))


def _run_ddg_search(query: str, max_results: int) -> tuple[list[dict], str] | tuple[None, str]:
    failures: list[str] = []
    saw_rate_limit = False
    for backend in _DDG_BACKENDS:
        for attempt in range(1, _DDG_ATTEMPTS_PER_BACKEND + 1):
            try:
                with DDGS() as ddgs:
                    results = list(ddgs.text(query, max_results=max_results, backend=backend))
                return results, backend
            except Exception as exc:
                failures.append(f"{backend}#{attempt}: {exc}")
                if _is_ddg_rate_limited(exc):
                    saw_rate_limit = True
                    if attempt < _DDG_ATTEMPTS_PER_BACKEND:
                        time.sleep(_DDG_RETRY_BASE_SECONDS * attempt)
                else:
                    break
    if saw_rate_limit:
        return None, (
            "Search temporarily unavailable due to provider rate limiting. "
            "Do not retry web_search immediately. Ask the user for a specific URL and use web_fetch instead."
        )
    if failures:
        return None, f"Search failed across backends: {' | '.join(failures[:3])}"
    return None, "Search failed: unknown error"


async def _generate_image_with_gemini(args: dict) -> str:
    from app.image.gemini_client import generate_images as _gen_images
    from app.config import get_runtime

    prompt = args.get("prompt", "")
    if not prompt:
        return "Error: no prompt provided"
    api_key = (args.get("api_key") or get_runtime("gemini_api_key") or "").strip()
    if not api_key:
        return "Error: Gemini API key is not configured. Set gemini_api_key in settings or pass api_key in tool arguments."
    model = args.get("model", get_runtime("gemini_image_model") or "imagen-3.0-generate-002")
    aspect_ratio = args.get("aspect_ratio", get_runtime("gemini_image_aspect_ratio") or "16:9")
    try:
        number_of_images = int(args.get("number_of_images", get_runtime("gemini_image_count") or 1))
    except (TypeError, ValueError):
        return "Error: number_of_images must be an integer"
    if number_of_images < 1 or number_of_images > 4:
        return "Error: number_of_images must be between 1 and 4"
    try:
        urls = await _gen_images(
            prompt=prompt, api_key=api_key, model=model,
            aspect_ratio=aspect_ratio, number_of_images=number_of_images,
        )
        if not urls:
            return "Gemini image generation completed but no image URL was returned."
        return "\n".join(f"![gemini generated image]({u})" for u in urls)
    except Exception as e:
        logger.exception("Gemini image generation failed")
        return f"Gemini image generation failed: {e}"


# ---------------------------------------------------------------------------
# --- GENERAL TOOLS ---
# ---------------------------------------------------------------------------

async def _tool_datetime_now(_args: dict) -> str:
    return datetime.now(timezone.utc).isoformat()

register(Skill(
    name="datetime_now", category="general",
    description="Get the current date and time in UTC.",
    parameters={}, status_prefix="Getting current time",
    handler=_tool_datetime_now,
))

# ---------------------------------------------------------------------------

async def _tool_calculate(args: dict) -> str:
    expression = args.get("expression", "")
    if not expression:
        return "Error: no expression provided"
    try:
        result = eval(expression, {"__builtins__": {}}, SAFE_MATH)  # noqa: S307
        return str(result)
    except Exception as e:
        return f"Error: {e}"

register(Skill(
    name="calculate", category="general",
    description="Evaluate a safe mathematical expression.",
    parameters={"expression": "A mathematical expression string"},
    status_prefix="Calculating",
    handler=_tool_calculate,
))

# ---------------------------------------------------------------------------

async def _tool_web_search(args: dict) -> str:
    query = args.get("query", "")
    if not query:
        return "Error: no query provided"
    try:
        max_results = int(args.get("max_results", 5))
    except (TypeError, ValueError):
        return "Error: max_results must be an integer"
    max_results = max(1, min(max_results, _DDG_MAX_RESULTS))
    try:
        results, detail = await asyncio.to_thread(_run_ddg_search, query, max_results)
        if results is None:
            logger.warning("web_search failed: %s", detail)
            return detail
        if not results:
            return "No results found."
        lines = []
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.get('title', '')}\n   {r.get('href', '')}\n   {r.get('body', '')}")
        return "\n\n".join(lines)
    except Exception as e:
        logger.warning("web_search failed: %s", e)
        return f"Search failed: {e}"

register(Skill(
    name="web_search", category="general",
    description="Search the web for current information using DuckDuckGo. Use for recent events, facts you're unsure about, or up-to-date information.",
    parameters={"query": "The search query string", "max_results": "Maximum results (default 5, max 10)"},
    status_prefix="Searching the web",
    handler=_tool_web_search,
))

# ---------------------------------------------------------------------------

async def _tool_web_fetch(args: dict) -> str:
    url = str(args.get("url", "")).strip()
    if not url:
        return "Error: no url provided"
    validation_error = await _validate_public_url(url)
    if validation_error:
        return validation_error
    try:
        max_chars = int(args.get("max_chars", 6000))
    except (TypeError, ValueError):
        return "Error: max_chars must be an integer"
    max_chars = max(500, min(max_chars, 20000))
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": "OutageOtto/0.1 (+local web_fetch tool)",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
            resp.raise_for_status()
            content_type = (resp.headers.get("content-type") or "").lower()
            body = resp.text
        title = _extract_title(body)
        text = _html_to_text(body) if "html" in content_type else body.strip()
        if not text:
            return f"Fetched {url} but no readable text content was extracted."
        clipped = text[:max_chars]
        title_line = f"Title: {title}\n" if title else ""
        suffix = "\n...[truncated]" if len(text) > len(clipped) else ""
        return f"URL: {url}\n{title_line}Content:\n{clipped}{suffix}"
    except Exception as e:
        logger.warning("web_fetch failed for %s: %s", url, e)
        return f"Fetch failed: {e}"

register(Skill(
    name="web_fetch", category="general",
    description="Fetch and extract readable text from a webpage URL. Use after web_search to open/read/summarize specific pages.",
    parameters={"url": "The full http(s) URL to fetch", "max_chars": "Maximum characters to return (default 6000)"},
    status_prefix="Fetching page",
    handler=_tool_web_fetch,
))

# ---------------------------------------------------------------------------

async def _tool_generate_image(args: dict) -> str:
    return await _generate_image_with_gemini(args)

register(Skill(
    name="generate_image", category="general",
    description="Generate an image using Google Gemini Imagen. Returns viewable image URLs.",
    parameters={
        "prompt": "Description of the image to generate",
        "api_key": "Optional Gemini API key",
        "model": "Optional model (default imagen-3.0-generate-002)",
        "aspect_ratio": "Optional aspect ratio (1:1, 16:9, 9:16)",
        "number_of_images": "Optional number of images (1-4)",
    },
    status_prefix="Generating image",
    handler=_tool_generate_image,
))

register(Skill(
    name="generate_image_gemini", category="general",
    description="Generate an image using Google Gemini Imagen (explicit alias).",
    parameters={
        "prompt": "Description of the image to generate",
        "api_key": "Optional Gemini API key",
        "model": "Optional model",
        "aspect_ratio": "Optional aspect ratio",
        "number_of_images": "Optional number of images (1-4)",
    },
    status_prefix="Generating image",
    handler=_tool_generate_image,
))

# ---------------------------------------------------------------------------
# --- STRATA WORKFLOW TOOLS ---
# ---------------------------------------------------------------------------

async def _tool_strata_stack_check(args: dict) -> str:
    timeout = float(_to_int(args.get("timeout_seconds"), default=8, minimum=3, maximum=60))
    checks: dict[str, dict[str, str]] = {}

    try:
        guardian_response = await _guardian_get("/api/overview")
        checks["guardian_api"] = {"status": "ok", "details": _truncate(guardian_response, 500)}
    except Exception as e:
        checks["guardian_api"] = {"status": "error", "details": str(e)}

    try:
        doctor_response = await _doctor_get("/api/v1/query?query=up")
        checks["doctor_query"] = {"status": "ok", "details": _truncate(doctor_response, 500)}
    except Exception as e:
        checks["doctor_query"] = {"status": "error", "details": str(e)}

    try:
        monofs_response = await _run_subprocess(["monofs-session", "--help"], timeout=timeout)
        checks["monofs_session_cli"] = {"status": "ok", "details": _truncate(monofs_response, 400)}
    except Exception as e:
        checks["monofs_session_cli"] = {"status": "error", "details": str(e)}

    guardianctl_cmd = _guardianctl_base_command()
    if guardianctl_cmd:
        try:
            guardianctl_response = await _run_subprocess(
                guardianctl_cmd + ["partition", "list"],
                timeout=timeout,
                env=await _monofs_env(),
            )
            failed = guardianctl_response.startswith("Error")
            checks["guardianctl"] = {
                "status": "error" if failed else "ok",
                "details": _truncate(guardianctl_response, 700),
            }
        except Exception as e:
            checks["guardianctl"] = {"status": "error", "details": str(e)}
    else:
        checks["guardianctl"] = {"status": "error", "details": "guardianctl binary not found (set GUARDIANCTL_BIN or install guardianctl in PATH)."}

    return json.dumps(
        {
            "summary": "Strata stack readiness check",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "checks": checks,
        },
        indent=2,
    )


register(Skill(
    name="strata_stack_check", category="strata",
    description="Check whether Doctor, Guardian, MonoFS CLI, and guardianctl are reachable/configured. Use only when a tool is failing or before deployment work; do not call it for routine telemetry queries.",
    parameters={"timeout_seconds": "Optional command timeout per check (default 8)"},
    status_prefix="Checking Strata stack readiness",
    handler=_tool_strata_stack_check,
))

# ---------------------------------------------------------------------------

async def _tool_agent_sso_status(_args: dict) -> str:
    return json.dumps(await oidc.status(), indent=2)


register(Skill(
    name="agent_sso_status", category="strata",
    description="Show the agent's OIDC/SSO state: configured, authenticated, source, subject, expiry, and device-flow availability. Use when Doctor or Guardian returns 401.",
    parameters={},
    status_prefix="Checking agent SSO status",
    handler=_tool_agent_sso_status,
))

# ---------------------------------------------------------------------------
# --- GUARDIAN TOOLS ---
# ---------------------------------------------------------------------------

async def _tool_guardian_overview(_args: dict) -> str:
    try:
        data = await _guardian_get("/api/overview")
        return _truncate(data)
    except Exception as e:
        logger.warning("guardian_overview failed: %s", e)
        return f"Guardian overview query failed: {e}"

register(Skill(
    name="guardian_overview", category="guardian",
    description="Get the Guardian dashboard overview: all partitions with health status, asset counts, intent summaries. Use this FIRST when checking infrastructure health.",
    parameters={},
    status_prefix="Querying Guardian dashboard",
    handler=_tool_guardian_overview,
))

# ---------------------------------------------------------------------------

async def _tool_guardian_partitions(_args: dict) -> str:
    try:
        data = await _guardian_get("/api/partitions")
        return _truncate(data)
    except Exception as e:
        logger.warning("guardian_partitions failed: %s", e)
        return f"Guardian partition list failed: {e}"

register(Skill(
    name="guardian_partitions", category="guardian",
    description="List all Guardian partitions with basic metadata.",
    parameters={},
    status_prefix="Listing Guardian partitions",
    handler=_tool_guardian_partitions,
))

# ---------------------------------------------------------------------------

async def _tool_guardian_partition_detail(args: dict) -> str:
    partition = args.get("partition", "")
    if not partition:
        return "Error: partition name is required"
    try:
        data = await _guardian_get(f"/api/partitions/{partition}")
        return _truncate(data)
    except Exception as e:
        logger.warning("guardian_partition_detail(%s) failed: %s", partition, e)
        return f"Guardian partition detail for '{partition}' failed: {e}"

register(Skill(
    name="guardian_partition_detail", category="guardian",
    description="Get full detail for a partition: manifest, runtime state, all intents with asset health, topology, recent events. Use this to drill into a specific partition.",
    parameters={"partition": "Partition name (e.g. agent, monofs, doctor, monitoring, k8s-top)"},
    status_prefix="Querying partition detail",
    handler=_tool_guardian_partition_detail,
))

# ---------------------------------------------------------------------------

async def _tool_guardian_reconcile(args: dict) -> str:
    partition = args.get("partition", "")
    if not partition:
        return "Error: partition name is required"
    try:
        data = await _guardian_post(f"/api/partitions/{partition}/reconcile")
        return _truncate(data)
    except Exception as e:
        logger.warning("guardian_reconcile(%s) failed: %s", partition, e)
        return f"Guardian reconcile for '{partition}' failed: {e}"

register(Skill(
    name="guardian_reconcile", category="guardian",
    description="Force an immediate reconciliation of a partition. Use when you need to trigger a deployment check or fix drift.",
    parameters={
        "partition": "Partition name to reconcile",
        "confirm": "Required for mutating tools. Set true/yes after user approval.",
    },
    status_prefix="Reconciling partition",
    mutating=True,
    handler=_tool_guardian_reconcile,
))

# ---------------------------------------------------------------------------

async def _tool_guardian_intent_activity(args: dict) -> str:
    partition = args.get("partition", "")
    intent = args.get("intent", "")
    if not partition or not intent:
        return "Error: both partition and intent names are required"
    try:
        data = await _guardian_get(f"/api/partitions/{partition}/intents/{intent}/activity")
        return _truncate(data)
    except Exception as e:
        logger.warning("guardian_intent_activity(%s, %s) failed: %s", partition, intent, e)
        return f"Guardian intent activity for '{partition}/{intent}' failed: {e}"

register(Skill(
    name="guardian_intent_activity", category="guardian",
    description="Get detailed intent activity: current status, drift report, observed health, last task logs, timestamps. Use when an intent is unhealthy or drifted.",
    parameters={
        "partition": "Partition name containing the intent",
        "intent": "Intent name to inspect",
    },
    status_prefix="Querying intent activity",
    handler=_tool_guardian_intent_activity,
))

# ---------------------------------------------------------------------------

async def _tool_guardian_catalog(_args: dict) -> str:
    try:
        data = await _guardian_get("/api/catalog")
        return _truncate(data, 4000)
    except Exception as e:
        logger.warning("guardian_catalog failed: %s", e)
        return f"Guardian catalog query failed: {e}"

register(Skill(
    name="guardian_catalog", category="guardian",
    description="List all available asset types and pushers in Guardian. Shows what infrastructure resources can be created.",
    parameters={},
    status_prefix="Querying asset catalog",
    handler=_tool_guardian_catalog,
))

# ---------------------------------------------------------------------------

async def _tool_guardian_asset_describe(args: dict) -> str:
    asset_type = str(args.get("type", "")).strip()
    if not asset_type:
        return "Error: type is required"

    base_cmd = _guardianctl_base_command()
    if not base_cmd:
        return "Error: guardianctl binary not found. Install guardianctl or set GUARDIANCTL_BIN."

    cmd = base_cmd + ["asset", "describe", "-type", asset_type]
    for key, flag in (
        ("partition", "-partition"),
        ("intent", "-intent"),
        ("asset", "-asset"),
    ):
        value = str(args.get(key, "")).strip()
        if value:
            cmd.extend([flag, value])

    try:
        return _truncate(await _run_subprocess(cmd, timeout=20.0, env=await _monofs_env()))
    except Exception as e:
        logger.warning("guardian_asset_describe(%s) failed: %s", asset_type, e)
        return f"Guardian asset describe for '{asset_type}' failed: {e}"


register(Skill(
    name="guardian_asset_describe", category="guardian",
    description="Show the template fields and hints for a Guardian asset type before authoring it. Use for Compute, ImageBuild, Volume, Config, Secret, LoadBalancer, Network, ObjectStore, Database, SQLDatabase, CDKStack, Observability, or TraefikRoute.",
    parameters={
        "type": "Asset type (required), e.g. Compute",
        "partition": "Optional partition for manifest-aware hints",
        "intent": "Optional intent for manifest-aware hints",
        "asset": "Optional asset name for manifest-aware hints",
    },
    status_prefix="Describing Guardian asset type",
    handler=_tool_guardian_asset_describe,
))

# ---------------------------------------------------------------------------

async def _tool_guardian_intent_plan(args: dict) -> str:
    partition = str(args.get("partition", "")).strip()
    if not partition:
        return "Error: partition is required"

    base_cmd = _guardianctl_base_command()
    if not base_cmd:
        return "Error: guardianctl binary not found. Install guardianctl or set GUARDIANCTL_BIN."

    cmd = base_cmd + ["intent", "plan", "-partition", partition]
    try:
        return _truncate(await _run_subprocess(cmd, timeout=60.0, env=await _monofs_env()))
    except Exception as e:
        logger.warning("guardian_intent_plan(%s) failed: %s", partition, e)
        return f"Guardian intent plan for '{partition}' failed: {e}"


register(Skill(
    name="guardian_intent_plan", category="guardian",
    description="Compile a dry-run plan for a Guardian partition. Use after editing partition, intent, or asset manifests and before proposing a release.",
    parameters={
        "partition": "Partition name to plan (required)",
    },
    status_prefix="Compiling Guardian intent plan",
    handler=_tool_guardian_intent_plan,
))

# ---------------------------------------------------------------------------

async def _tool_guardian_partition_validate(args: dict) -> str:
    root = str(args.get("root", "./stratatools/partitions")).strip() or "./stratatools/partitions"
    base_cmd = _guardianctl_base_command()
    if not base_cmd:
        return "Error: guardianctl binary not found. Install guardianctl or set GUARDIANCTL_BIN."

    cmd = base_cmd + ["partition", "validate-local", "-root", root]
    try:
        return _truncate(await _run_subprocess(cmd, timeout=60.0, env=await _monofs_env()))
    except Exception as e:
        logger.warning("guardian_partition_validate(%s) failed: %s", root, e)
        return f"Guardian partition validation for '{root}' failed: {e}"


register(Skill(
    name="guardian_partition_validate", category="guardian",
    description="Parse and validate local Guardian partition bundles under a root directory. Use before proposing a partition, intent, or asset change.",
    parameters={
        "root": "Directory containing partition subdirectories (default ./stratatools/partitions)",
    },
    status_prefix="Validating Guardian partition bundle",
    handler=_tool_guardian_partition_validate,
))

# ---------------------------------------------------------------------------
# 
async def _tool_guardian_deploy_partition(args: dict) -> str:
    partition_dir = str(args.get("partition_dir", "")).strip()
    if not partition_dir:
        return "Error: partition_dir is required"

    partition = str(args.get("partition", "")).strip() or os.path.basename(os.path.abspath(partition_dir))
    include_secrets = _to_bool(args.get("include_secrets"), default=False)
    remove_missing = _to_bool(args.get("remove_missing"), default=True)
    run_reconcile = _to_bool(args.get("reconcile"), default=True)
    wait_for_health = _to_bool(args.get("wait"), default=True)
    timeout_seconds = _to_int(args.get("timeout_seconds"), default=600, minimum=30, maximum=7200)
    desired_status = str(args.get("desired_status", "Healthy")).strip() or "Healthy"
    plan_only = _to_bool(args.get("plan_only"), default=True)

    base_cmd = _guardianctl_base_command()
    if not base_cmd:
        return "Error: guardianctl binary not found. Install guardianctl or set GUARDIANCTL_BIN."

    push_cmd = base_cmd + ["partition", "push", "-dir", partition_dir]
    if include_secrets:
        push_cmd.append("-include-secrets")
    if not remove_missing:
        push_cmd.extend(["-remove-missing=false"])

    reconcile_cmd = base_cmd + ["partition", "reconcile", "-partition", partition]
    wait_cmd = base_cmd + [
        "partition", "wait",
        "-partition", partition,
        "-status", desired_status,
        "-timeout", f"{timeout_seconds}s",
    ]

    command_plan = [push_cmd]
    if run_reconcile:
        command_plan.append(reconcile_cmd)
    if wait_for_health:
        command_plan.append(wait_cmd)

    if plan_only:
        return "\n".join([
            "Deployment proposal generated. No commands were executed.",
            "To execute, call guardian_deploy_partition with plan_only=false and confirm=true.",
            "",
            "Planned commands:",
            *[f"{idx}. {' '.join(cmd)}" for idx, cmd in enumerate(command_plan, 1)],
        ])

    outputs: list[str] = []
    auth_env = await _monofs_env()
    for idx, cmd in enumerate(command_plan, 1):
        result = await _run_subprocess(cmd, timeout=float(timeout_seconds), env=auth_env)
        outputs.append(f"Step {idx}: {' '.join(cmd)}\n{_truncate(result, 4000)}")
        if result.startswith("Error"):
            break
    return "\n\n".join(outputs)


register(Skill(
    name="guardian_deploy_partition", category="guardian",
    description="Deploy a partition with guardianctl (push, optional reconcile, optional wait). Default mode is proposal-only.",
    parameters={
        "partition_dir": "Local partition directory path (required)",
        "partition": "Partition name override (defaults to partition_dir basename)",
        "reconcile": "Whether to trigger reconcile after push (default true)",
        "wait": "Whether to wait for desired status after reconcile (default true)",
        "desired_status": "Desired final status for wait (default Healthy)",
        "timeout_seconds": "Timeout for wait and command steps (default 600)",
        "include_secrets": "Include secrets/ directory during push (default false)",
        "remove_missing": "Remove managed files missing from local dir (default true)",
        "plan_only": "When true, return proposal/commands only (default true)",
        "confirm": "Required for mutating tools. Set true/yes after user approval.",
    },
    status_prefix="Deploying partition with guardianctl",
    mutating=True,
    handler=_tool_guardian_deploy_partition,
))

# ---------------------------------------------------------------------------
# --- DOCTOR TOOLS ---
# ---------------------------------------------------------------------------

def _log_record_partition(record: dict) -> str:
    attrs = record.get("attributes") or {}
    return str(
        record.get("partition")
        or attrs.get("guardian.partition")
        or attrs.get("guardian_partition")
        or ""
    )


def _log_record_matches(
    record: dict,
    *,
    service: str = "",
    component: str = "",
    asset: str = "",
) -> bool:
    attrs = record.get("attributes") or {}
    record_component = str(attrs.get("guardian.component") or "")
    record_asset = str(attrs.get("guardian.asset") or "")
    if component and record_component != component:
        return False
    if asset and record_asset != asset:
        return False
    if service:
        candidates = (
            str(record.get("service") or ""),
            record_component,
            record_asset,
            str(attrs.get("service.alias") or ""),
            str(attrs.get("service.name") or ""),
        )
        if not any(candidate and service in candidate for candidate in candidates):
            return False
    return True


async def _tool_doctor_logs_services(args: dict) -> str:
    partition = str(args.get("partition", "")).strip()
    params = ["tenant=default"]
    if partition:
        params.append(f"partition={quote_plus(partition)}")
    try:
        raw = await _doctor_get(f"/v1/logs/services?{'&'.join(params)}")
        payload = json.loads(raw)
        services = payload.get("services") or []
        partitions = payload.get("partitions") or []
        payload["service_count"] = len(services)
        payload["partition_count"] = len(partitions)
        payload["note"] = (
            "services/partitions are Doctor's native log inventory. "
            "log_groups is AWS/CloudWatch-only and is normally empty here."
        )
        return _truncate(json.dumps(payload, indent=2))
    except Exception as e:
        logger.warning("doctor_logs_services failed: %s", e)
        return f"Doctor log services query failed: {e}"

register(Skill(
    name="doctor_logs_services", category="doctor",
    description="List services/partitions with native log data in Doctor. `services` is the log inventory; `log_groups` is AWS/CloudWatch-only and is normally empty. Filter by partition to scope to agent, doctor, monofs, etc.",
    parameters={"partition": "Optional Guardian partition filter (e.g. agent)"},
    status_prefix="Querying Doctor log services",
    handler=_tool_doctor_logs_services,
))

# ---------------------------------------------------------------------------

async def _tool_doctor_logs_search(args: dict) -> str:
    service = str(args.get("service", "")).strip()
    component = str(args.get("component", "")).strip()
    asset = str(args.get("asset", "")).strip()
    query = str(args.get("query", "")).strip()
    partition = str(args.get("partition", "")).strip()
    from_ts = str(args.get("from", "")).strip()
    to_ts = str(args.get("to", "")).strip()
    limit = _to_int(args.get("limit"), default=20, minimum=1, maximum=100)

    # Doctor applies the partition filter *after* the row limit, so a small
    # limit can return zero even when matching records exist. Over-fetch, then
    # filter and truncate here. Short service names / components / assets are
    # matched locally because the stored service field is fully qualified
    # (e.g. monofs.monofs-storage.router-a).
    client_filter = bool(component or asset or (service and "." not in service))
    scan_limit = 1000 if (partition or client_filter) else limit

    def build_params(*, include_service: bool, scan: int) -> list[str]:
        params = ["tenant=default", f"limit={scan}"]
        if partition:
            params.append(f"partition={quote_plus(partition)}")
        if include_service and service:
            params.append(f"service={quote_plus(service)}")
        if query:
            params.append(f"q={quote_plus(query)}")
        if from_ts:
            params.append(f"from={quote_plus(from_ts)}")
        if to_ts:
            params.append(f"to={quote_plus(to_ts)}")
        return params

    async def fetch(include_service: bool, scan: int) -> dict | None:
        params = build_params(include_service=include_service, scan=scan)
        raw = await _doctor_get(f"/v1/logs/search?{'&'.join(params)}")
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return payload

    try:
        payload = await fetch(include_service=not client_filter, scan=scan_limit)
        if payload is None:
            return "Doctor returned a non-JSON log payload."

        results = payload.get("results") or []
        if client_filter:
            results = [
                record
                for record in results
                if isinstance(record, dict)
                and _log_record_matches(
                    record, service=service, component=component, asset=asset
                )
            ]

        # If a fully-qualified service selector returned nothing, retry with a
        # broad scan and match the service/component/asset locally.
        if not results and service and not client_filter:
            payload = await fetch(include_service=False, scan=1000)
            if payload is not None:
                results = [
                    record
                    for record in (payload.get("results") or [])
                    if isinstance(record, dict)
                    and _log_record_matches(record, service=service)
                ]
                scan_limit = 1000

        payload["filters"] = {
            "partition": partition,
            "service": service,
            "component": component,
            "asset": asset,
            "query": query,
        }
        payload["scanned"] = scan_limit
        payload["returned"] = len(results)
        payload["shown"] = min(len(results), limit)
        payload["results"] = results[:limit]
        return _truncate(json.dumps(payload, indent=2), 20000)
    except Exception as e:
        logger.warning("doctor_logs_search failed: %s", e)
        return f"Doctor log search failed: {e}"

register(Skill(
    name="doctor_logs_search", category="doctor",
    description="Search logs in Doctor telemetry. Filter by partition, Guardian component/asset, fully-qualified service, text query, and time range. Use component/asset for short names like monofs-router or router-a; the tool over-fetches and filters locally so partition limits do not hide records. log_groups is AWS-only and is not a log inventory.",
    parameters={
        "partition": "Guardian partition to filter (optional, e.g. monofs)",
        "service": "Service name: fully qualified or short asset/component name (optional)",
        "component": "Guardian component filter, e.g. monofs-router (optional)",
        "asset": "Guardian asset filter, e.g. router-a (optional)",
        "query": "Text to search in log bodies (optional; omit to list recent logs)",
        "from": "Start time RFC3339 (optional)",
        "to": "End time RFC3339 (optional)",
        "limit": "Max results to show (default 20, max 100; the tool scans up to 1000)",
    },
    status_prefix="Searching Doctor logs",
    handler=_tool_doctor_logs_search,
))

# ---------------------------------------------------------------------------
# Native Loki (LogQL) and Tempo (TraceQL) query tools
# ---------------------------------------------------------------------------

def _compact_loki_payload(raw: str, max_lines: int = 200) -> str:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return _truncate(raw)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return _truncate(raw)
    result_type = str(data.get("resultType") or "")
    result = data.get("result")
    if result_type == "matrix":
        return _compact_metric_payload(raw)

    lines = []
    if isinstance(result, list):
        for stream in result:
            if not isinstance(stream, dict):
                continue
            labels = stream.get("stream") or {}
            values = stream.get("values") or []
            for item in values:
                if isinstance(item, list) and len(item) >= 2:
                    lines.append(
                        {"time": item[0], "labels": labels, "line": item[1]}
                    )
    return json.dumps(
        {
            "result_type": result_type,
            "streams": len(result) if isinstance(result, list) else 0,
            "lines": len(lines),
            "results": lines[:max_lines],
        },
        indent=2,
    )


def _compact_tempo_search(raw: str, max_traces: int = 100) -> str:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return _truncate(raw)
    traces = payload.get("traces") if isinstance(payload, dict) else None
    if not isinstance(traces, list):
        return _truncate(raw)
    compact = []
    for trace in traces[:max_traces]:
        if not isinstance(trace, dict):
            continue
        span_sets = trace.get("spanSets") or []
        matched = sum(int(item.get("matched") or 0) for item in span_sets if isinstance(item, dict))
        compact.append(
            {
                "trace_id": trace.get("traceID"),
                "root_service": trace.get("rootServiceName"),
                "root_trace": trace.get("rootTraceName"),
                "start_time": trace.get("startTimeUnixNano"),
                "duration_ms": trace.get("durationMs"),
                "matched_spans": matched,
                "span_names": [
                    span.get("name")
                    for span_set in span_sets
                    for span in (span_set.get("spans") or [])
                    if isinstance(span, dict) and span.get("name")
                ][:10],
            }
        )
    return json.dumps({"count": len(traces), "returned": len(compact), "traces": compact}, indent=2)


_SEVERITY_EQ_RE = re.compile(
    r'\b(level|severity_text)\s*=\s*"([a-zA-Z]+)"', re.IGNORECASE
)


def _normalize_logql_severity(query: str) -> str:
    """Doctor stores severity uppercase but exposes lowercase stream labels.

    Rewrite an equality matcher like level="warn" to a case-insensitive regex
    so the same selector works against the stored value.
    """
    return _SEVERITY_EQ_RE.sub(
        lambda match: f'{match.group(1)}=~"(?i){match.group(2)}"',
        query,
    )


async def _tool_doctor_logql_query(args: dict) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "Error: LogQL query is required (e.g. '{service=~\".+\"} |= \"error\"')"
    query = _normalize_logql_severity(query)
    limit = _to_int(args.get("limit"), default=100, minimum=1, maximum=1000)
    direction = str(args.get("direction", "backward")).strip() or "backward"
    params = [
        f"query={quote_plus(query)}",
        f"limit={limit}",
        f"direction={quote_plus(direction)}",
    ]
    start = str(args.get("start", "")).strip()
    end = str(args.get("end", "")).strip()
    if start:
        params.append(f"start={quote_plus(start)}")
    if end:
        params.append(f"end={quote_plus(end)}")
    try:
        raw = await _doctor_get(f"/loki/api/v1/query_range?{'&'.join(params)}")
        compact = _compact_loki_payload(raw, max_lines=limit)
        # If the query returned no lines and looks like a severity body filter,
        # explain the real label vocabulary instead of leaving a blank result.
        empty = False
        try:
            payload = json.loads(compact)
            empty = not (payload.get("results") or [])
        except (TypeError, ValueError):
            pass
        if empty:
            lowered = query.lower()
            severity_filter = any(
                token in lowered for token in ('warn', 'info', 'error', 'debug')
            )
            has_level_label = "level" in lowered
            if severity_filter and not has_level_label:
                payload = json.loads(compact)
                payload["hint"] = (
                    "No lines matched. Severity is a stream label in Doctor, not "
                    "body text. Stored severity values are uppercase while the "
                    'stream label is lowercase, so use a case-insensitive regex: '
                    '{guardian_partition="monofs", level=~"(?i)warn"} or '
                    '{level=~"(?i)warn|error"}. Call doctor_logql_schema for the '
                    "actual labels and values."
                )
                return json.dumps(payload, indent=2)
        return compact
    except Exception as e:
        logger.warning("doctor_logql_query failed: %s", e)
        return f"Doctor LogQL query failed: {e}"


register(Skill(
    name="doctor_logql_query", category="doctor",
    description="Query Doctor with native Loki LogQL (/loki/api/v1/query_range). Supports stream selectors, line filters, label filters, parsers, and metric queries. Call doctor_logql_schema first to see labels; severity is the `level` stream label (level=\"warn\"), not body text. Use this when the simplified doctor_logs_search is not expressive enough.",
    parameters={
        "query": "LogQL query, e.g. '{guardian_partition=\"monofs\"} |= \"metadata not found\"'",
        "start": "Start time (RFC3339 or Unix timestamp; default 1h ago)",
        "end": "End time (RFC3339 or Unix timestamp; default now)",
        "limit": "Max log lines (default 100, max 1000)",
        "direction": "backward or forward (default backward)",
    },
    status_prefix="Querying Doctor LogQL",
    handler=_tool_doctor_logql_query,
))


async def _tool_doctor_logql_schema(args: dict) -> str:
    """Expose Doctor's actual Loki stream labels and the values agents need."""
    try:
        raw = await _doctor_get("/loki/api/v1/labels")
        payload = json.loads(raw)
    except Exception as e:
        logger.warning("doctor_logql_schema failed: %s", e)
        return f"Doctor LogQL labels query failed: {e}"
    labels = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(labels, list):
        return _truncate(raw)
    labels = sorted(str(label) for label in labels)

    values: dict[str, list[str]] = {}
    for label in ("level", "guardian_partition", "guardian_component", "guardian_asset", "service"):
        if label not in labels:
            continue
        try:
            value_raw = await _doctor_get(
                f"/loki/api/v1/label/{quote_plus(label)}/values"
            )
            value_payload = json.loads(value_raw)
            data = value_payload.get("data") if isinstance(value_payload, dict) else None
            if isinstance(data, list):
                values[label] = [str(value) for value in data[:50]]
        except Exception:
            values[label] = []

    return json.dumps(
        {
            "labels": labels,
            "values": values,
            "examples": [
                '{guardian_partition="monofs", level=~"(?i)warn"}',
                '{guardian_partition="monofs", guardian_component="monofs-server"} |= "metadata not found"',
                '{guardian_partition="monofs"} |~ "metadata.*not found"',
            ],
            "note": (
                "level is a stream label. Stored severity is uppercase (WARN) "
                "while the exposed label value is lowercase (warn), so prefer "
                'level=~"(?i)warn" or level=~"(?i)warn|error". Do not search for '
                "the word WARN/INFO in the log body unless the message actually "
                "contains it."
            ),
        },
        indent=2,
    )


register(Skill(
    name="doctor_logql_schema", category="doctor",
    description="Return Doctor's actual Loki stream labels and key label values (level, guardian_partition, guardian_component, guardian_asset, service) plus example selectors. Call this before writing a LogQL query so level/partition filters use the real label names.",
    parameters={},
    status_prefix="Inspecting Doctor log label schema",
    handler=_tool_doctor_logql_schema,
))


async def _tool_doctor_logql_label_values(args: dict) -> str:
    label = str(args.get("label", "")).strip()
    if not label:
        return "Error: label is required (e.g. level, guardian_partition, guardian_component)"
    params = []
    start = str(args.get("start", "")).strip()
    end = str(args.get("end", "")).strip()
    if start:
        params.append(f"start={quote_plus(start)}")
    if end:
        params.append(f"end={quote_plus(end)}")
    path = f"/loki/api/v1/label/{quote_plus(label)}/values"
    if params:
        path += "?" + "&".join(params)
    try:
        return _truncate(await _doctor_get(path), 12000)
    except Exception as e:
        logger.warning("doctor_logql_label_values(%s) failed: %s", label, e)
        return f"Doctor LogQL label-values query failed: {e}"


register(Skill(
    name="doctor_logql_label_values", category="doctor",
    description="List values for one Doctor Loki stream label (e.g. label='level' returns error/info/warn; label='guardian_partition' returns doctor/guardian-configs/monofs).",
    parameters={
        "label": "Label name (required), e.g. level, guardian_partition, guardian_component, guardian_asset, service",
        "start": "Start time (optional)",
        "end": "End time (optional)",
    },
    status_prefix="Listing Doctor log label values",
    handler=_tool_doctor_logql_label_values,
))


async def _tool_doctor_traceql_query(args: dict) -> str:
    query = str(args.get("query", "")).strip()
    limit = _to_int(args.get("limit"), default=20, minimum=1, maximum=100)
    spans_per_trace = _to_int(args.get("spans_per_trace"), default=0, minimum=0, maximum=100)
    params = [f"limit={limit}"]
    if query:
        params.append(f"q={quote_plus(query)}")
    if spans_per_trace:
        params.append(f"spss={spans_per_trace}")
    start = str(args.get("start", "")).strip()
    end = str(args.get("end", "")).strip()
    if start:
        params.append(f"start={quote_plus(start)}")
    if end:
        params.append(f"end={quote_plus(end)}")
    try:
        raw = await _doctor_get(f"/api/search?{'&'.join(params)}")
        return _compact_tempo_search(raw, max_traces=limit)
    except Exception as e:
        logger.warning("doctor_traceql_query failed: %s", e)
        return f"Doctor TraceQL query failed: {e}"


register(Skill(
    name="doctor_traceql_query", category="doctor",
    description="Search traces with native Tempo TraceQL (Tempo /api/search). Supports TraceQL selectors like '{ status = \"error\" }', '{ status = STATUS_CODE_ERROR }', '{ resource.service.name = \"monofs-router\" }', duration filters, and tag matchers. Discover tags with doctor_traceql_tags. Use for trace-level queries beyond the recent-traces summary.",
    parameters={
        "query": "TraceQL query, e.g. '{ status = error }' or '{ resource.service.name = \"monofs-router\" }'",
        "start": "Start time (RFC3339 or Unix timestamp; default 1h ago)",
        "end": "End time (RFC3339 or Unix timestamp; default now)",
        "limit": "Max traces (default 20, max 100)",
        "spans_per_trace": "Include up to N matched spans per trace (default 0)",
    },
    status_prefix="Querying Doctor TraceQL",
    handler=_tool_doctor_traceql_query,
))


async def _tool_doctor_traceql_tags(args: dict) -> str:
    params = []
    start = str(args.get("start", "")).strip()
    end = str(args.get("end", "")).strip()
    if start:
        params.append(f"start={quote_plus(start)}")
    if end:
        params.append(f"end={quote_plus(end)}")
    path = "/api/search/tags"
    if params:
        path += "?" + "&".join(params)
    try:
        return _truncate(await _doctor_get(path), 12000)
    except Exception as e:
        logger.warning("doctor_traceql_tags failed: %s", e)
        return f"Doctor TraceQL tag query failed: {e}"


register(Skill(
    name="doctor_traceql_tags", category="doctor",
    description="List Tempo TraceQL tag names available in Doctor. Use before writing a TraceQL selector if you are unsure which attributes exist.",
    parameters={
        "start": "Start time (optional; default 1h ago)",
        "end": "End time (optional; default now)",
    },
    status_prefix="Listing Doctor TraceQL tags",
    handler=_tool_doctor_traceql_tags,
))


def _compact_traceql_metrics(raw: str, max_series: int = 100) -> str:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return _truncate(raw)
    series = payload.get("series") if isinstance(payload, dict) else None
    if not isinstance(series, list):
        return _truncate(raw)
    compact = []
    for item in series[:max_series]:
        if not isinstance(item, dict):
            continue
        labels = {
            label.get("key"): label.get("value")
            for label in (item.get("labels") or [])
            if isinstance(label, dict) and label.get("key")
        }
        samples = item.get("samples") or []
        compact.append(
            {
                "labels": labels,
                "points": len(samples),
                "last": samples[-1] if samples else None,
                "first": samples[0] if samples else None,
            }
        )
    return json.dumps({"series": len(series), "returned": len(compact), "results": compact}, indent=2)


async def _tool_doctor_traceql_metrics(args: dict) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "Error: TraceQL metrics query is required (e.g. '{ status = error } | count_over_time()')"
    params = [f"q={quote_plus(query)}"]
    start = str(args.get("start", "")).strip()
    end = str(args.get("end", "")).strip()
    step = str(args.get("step", "")).strip()
    if start:
        params.append(f"start={quote_plus(start)}")
    if end:
        params.append(f"end={quote_plus(end)}")
    if step:
        params.append(f"step={quote_plus(step)}")
    try:
        raw = await _doctor_get(f"/api/metrics/query_range?{'&'.join(params)}")
        return _compact_traceql_metrics(raw)
    except Exception as e:
        logger.warning("doctor_traceql_metrics failed: %s", e)
        return f"Doctor TraceQL metrics query failed: {e}"


register(Skill(
    name="doctor_traceql_metrics", category="doctor",
    description="Run a Tempo TraceQL metrics query (Tempo /api/metrics/query_range). Use for span-derived metrics such as count_over_time, rate, quantile_over_time grouped by service/status.",
    parameters={
        "query": "TraceQL metrics query, e.g. '{ status = error } | count_over_time() by (resource.service.name)'",
        "start": "Start time (optional; default 1h ago)",
        "end": "End time (optional; default now)",
        "step": "Resolution step, e.g. 30s (optional)",
    },
    status_prefix="Querying Doctor TraceQL metrics",
    handler=_tool_doctor_traceql_metrics,
))


# ---------------------------------------------------------------------------

_METRIC_LABEL_KEYS = (
    "__name__",
    "guardian_partition",
    "guardian_intent",
    "guardian_asset",
    "guardian_component",
    "k8s_pod_name",
    "k8s_namespace_name",
    "k8s_node_name",
    "node",
    "component",
    "job",
    "instance",
    "pod",
    "namespace",
)


def _compact_metric_payload(raw: str, max_series: int = 100) -> str:
    """Drop telemetry-resource noise from a Prometheus response.

    Without this, one series carries a dozen service/SDK labels and the tool
    result hits the truncation limit before the interesting values appear.
    """
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return _truncate(raw)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return _truncate(raw)
    result = data.get("result")
    if not isinstance(result, list):
        return _truncate(raw)

    compact = []
    for series in result[:max_series]:
        if not isinstance(series, dict):
            continue
        metric = series.get("metric") or {}
        labels = {
            key: metric[key]
            for key in _METRIC_LABEL_KEYS
            if key in metric and metric[key] not in (None, "")
        }
        entry = {"labels": labels}
        if "value" in series:
            entry["value"] = series["value"]
        elif "values" in series:
            values = series.get("values") or []
            entry["points"] = len(values)
            entry["last"] = values[-1] if values else None
            entry["first"] = values[0] if values else None
        compact.append(entry)

    out = {
        "result_type": data.get("resultType", ""),
        "series": len(result),
        "returned": len(compact),
        "results": compact,
    }
    return _truncate(json.dumps(out, indent=2), 20000)


async def _tool_doctor_metrics_query(args: dict) -> str:
    promql = args.get("promql", "")
    if not promql:
        return "Error: promql query is required"
    compact = _to_bool(args.get("compact"), default=True)
    params = [f"query={quote_plus(str(promql))}"]
    step = args.get("step", "15s")
    params.append(f"step={quote_plus(str(step))}")
    start = args.get("start", "")
    end = args.get("end", "")
    if start and end:
        params.append(f"start={quote_plus(str(start))}")
        params.append(f"end={quote_plus(str(end))}")
        path = "query_range"
    else:
        path = "query"
    try:
        data = await _doctor_get(f"/api/v1/{path}?{'&'.join(params)}")
        return _compact_metric_payload(data) if compact else _truncate(data)
    except Exception as e:
        logger.warning("doctor_metrics_query failed: %s", e)
        return f"Doctor metrics query failed: {e}"

register(Skill(
    name="doctor_metrics_query", category="doctor",
    description="Query metrics using PromQL. Returns compacted time-series data (labels and values) so the result fits in context. Use for CPU, memory, error rates, latency, request counts. Discover names with doctor_metrics_names first if unsure.",
    parameters={
        "promql": "PromQL query (e.g. 'rate(http_requests_total[5m])')",
        "start": "Start time (RFC3339, optional—omit for instant query)",
        "end": "End time (RFC3339, optional)",
        "step": "Resolution step (default 15s)",
        "compact": "Drop resource/SDK labels and summarize (default true)",
    },
    status_prefix="Querying Doctor metrics",
    handler=_tool_doctor_metrics_query,
))

# ---------------------------------------------------------------------------

def _span_is_error(span: dict) -> bool:
    code = str(span.get("status_code") or "").upper()
    if "ERROR" in code:
        return True
    attrs = span.get("attributes") or {}
    rpc_code = str(
        attrs.get("rpc.response.status_code")
        or attrs.get("rpc_response_status_code")
        or ""
    ).upper()
    if rpc_code and rpc_code not in {"OK", "0"}:
        return True
    http_code = attrs.get("http.status_code") or attrs.get("http_status_code")
    try:
        if http_code is not None and int(http_code) >= 500:
            return True
    except (TypeError, ValueError):
        pass
    return False


async def _trace_status_summary(trace_id: str, tenant: str = "default") -> dict:
    """Fetch a trace and summarize span statuses without dumping every span."""
    if not trace_id:
        return {}
    try:
        raw = await _doctor_get(f"/v1/traces/{trace_id}?tenant={quote_plus(tenant)}")
        payload = json.loads(raw)
    except Exception as exc:
        return {"detail_error": str(exc)}
    spans = payload.get("spans") or []
    if not isinstance(spans, list):
        return {"detail_error": "trace detail had no spans list"}
    statuses = Counter(str(span.get("status_code") or "UNSET") for span in spans)
    error_spans = []
    for span in spans:
        if _span_is_error(span):
            error_spans.append(
                {
                    "name": span.get("name"),
                    "service": span.get("service"),
                    "status_code": span.get("status_code"),
                }
            )
    return {
        "span_count": len(spans),
        "span_status": dict(statuses),
        "error_count": len(error_spans),
        "error_spans": error_spans[:5],
    }


async def _tool_doctor_metrics_names(args: dict) -> str:
    search = str(args.get("search", "")).strip().lower()
    limit = _to_int(args.get("limit"), default=200, minimum=1, maximum=1000)
    try:
        raw = await _doctor_get("/api/v1/label/__name__/values")
        payload = json.loads(raw)
    except Exception as e:
        logger.warning("doctor_metrics_names failed: %s", e)
        return f"Doctor metric-name query failed: {e}"
    names = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(names, list):
        return _truncate(raw)
    if search:
        names = [name for name in names if search in str(name).lower()]
    names = sorted(str(name) for name in names)
    return json.dumps(
        {"search": search, "count": len(names), "metric_names": names[:limit]},
        indent=2,
    )


register(Skill(
    name="doctor_metrics_names", category="doctor",
    description="List metric names available in Doctor (Prometheus label __name__), optionally filtered by substring. Call this before guessing a PromQL metric name. Use doctor_metrics_query afterwards with a real metric.",
    parameters={
        "search": "Optional substring filter, e.g. cpu, memory, container, http",
        "limit": "Max names to return (default 200, max 1000)",
    },
    status_prefix="Listing Doctor metric names",
    handler=_tool_doctor_metrics_names,
))


async def _tool_doctor_metrics_labels(args: dict) -> str:
    match = str(args.get("match", "")).strip()
    params = []
    if match:
        params.append(f"match[]={quote_plus(match)}")
    path = "/api/v1/labels"
    if params:
        path += "?" + "&".join(params)
    try:
        return _truncate(await _doctor_get(path))
    except Exception as e:
        logger.warning("doctor_metrics_labels failed: %s", e)
        return f"Doctor metric-label query failed: {e}"


register(Skill(
    name="doctor_metrics_labels", category="doctor",
    description="List label names for Doctor metrics, optionally scoped to a series match (e.g. match='container_cpu_usage_seconds_total'). Use to discover partition/node labels before writing a PromQL filter.",
    parameters={"match": "Optional PromQL series selector, e.g. up{guardian_partition=\"monofs\"}"},
    status_prefix="Listing Doctor metric labels",
    handler=_tool_doctor_metrics_labels,
))


async def _tool_doctor_traces_recent(args: dict) -> str:
    service = args.get("service", "")
    partition = args.get("partition", "")
    from_ts = args.get("from", "")
    to_ts = args.get("to", "")
    limit = _to_int(args.get("limit"), default=20, minimum=1, maximum=50)
    include_status = _to_bool(args.get("include_span_status"), default=True)
    errors_only = _to_bool(args.get("errors_only"), default=False)
    params = [f"tenant=default", f"limit={min(limit, 50)}"]
    if partition:
        params.append(f"partition={quote_plus(str(partition))}")
    if service:
        params.append(f"service={quote_plus(str(service))}")
    if from_ts:
        params.append(f"from={quote_plus(str(from_ts))}")
    if to_ts:
        params.append(f"to={quote_plus(str(to_ts))}")
    try:
        data = await _doctor_get(f"/v1/traces/recent?{'&'.join(params)}")
    except Exception as e:
        logger.warning("doctor_traces_recent failed: %s", e)
        return f"Doctor traces query failed: {e}"

    if not include_status:
        return _truncate(data)

    # The recent-traces endpoint only returns summary metadata; it does not
    # expose span status. Enrich the first few traces so "are there errors?"
    # can be answered from one tool call instead of a second guessing game.
    try:
        payload = json.loads(data)
    except (TypeError, ValueError):
        return _truncate(data)
    traces = payload.get("traces") if isinstance(payload, dict) else None
    if not isinstance(traces, list):
        return _truncate(data)

    semaphore = asyncio.Semaphore(8)

    async def enrich(trace: dict) -> None:
        async with semaphore:
            summary = await _trace_status_summary(str(trace.get("trace_id") or ""))
            trace.update(summary)

    await asyncio.gather(*(enrich(t) for t in traces if isinstance(t, dict)))

    # Put error traces first and surface a top-level count. The response is
    # often large enough to hit the tool truncation limit; the summary and the
    # interesting traces must survive that cut.
    traces.sort(key=lambda trace: int(trace.get("error_count") or 0), reverse=True)
    scanned = len(traces)
    total_error_spans = sum(int(t.get("error_count") or 0) for t in traces)
    traces_with_errors = sum(1 for t in traces if t.get("error_count"))
    if errors_only:
        traces = [trace for trace in traces if trace.get("error_count")]
    out = {
        "scanned": scanned,
        "returned": len(traces),
        "errors_only": errors_only,
        "error_summary": {
            "traces_with_errors": traces_with_errors,
            "error_spans": total_error_spans,
        },
        "traces": traces,
    }
    return _truncate(json.dumps(out, indent=2), 20000)

register(Skill(
    name="doctor_traces_recent", category="doctor",
    description="Get recent trace summaries from Doctor with span-status/error enrichment. Shows trace IDs, services, durations, span status counts, and error spans, plus `scanned` and `error_summary`. `errors_only` filters the trace list but still reports how many were scanned. Use this to answer whether traces contain errors.",
    parameters={
        "partition": "Guardian partition to filter (optional, e.g. agent)",
        "service": "Service name filter (optional)",
        "from": "Start time RFC3339 (optional)",
        "to": "End time RFC3339 (optional)",
        "limit": "Max traces (default 20, max 50)",
        "include_span_status": "Fetch span status/error details for each trace (default true)",
        "errors_only": "Return only traces that contain error spans (default false)",
    },
    status_prefix="Querying Doctor traces",
    handler=_tool_doctor_traces_recent,
))

# ---------------------------------------------------------------------------

async def _tool_doctor_trace_detail(args: dict) -> str:
    trace_id = args.get("trace_id", "")
    if not trace_id:
        return "Error: trace_id is required"
    try:
        data = await _doctor_get(f"/v1/traces/{trace_id}?tenant=default")
        return _truncate(data)
    except Exception as e:
        logger.warning("doctor_trace_detail(%s) failed: %s", trace_id, e)
        return f"Doctor trace detail for '{trace_id}' failed: {e}"

register(Skill(
    name="doctor_trace_detail", category="doctor",
    description="Get the full trace by ID — all spans with timings, status codes, attributes. Use after doctor_traces_recent to drill into a specific trace.",
    parameters={"trace_id": "Trace ID from doctor_traces_recent or error logs"},
    status_prefix="Querying trace detail",
    handler=_tool_doctor_trace_detail,
))

# ---------------------------------------------------------------------------

async def _tool_doctor_guardian_events(args: dict) -> str:
    partition = args.get("partition", "")
    limit = _to_int(args.get("limit"), default=20, minimum=1, maximum=100)
    params = [f"tenant=default", f"limit={min(limit, 100)}"]
    if partition:
        params.append(f"partition={quote_plus(str(partition))}")
    try:
        data = await _doctor_get(f"/v1/guardian/events?{'&'.join(params)}")
        return _truncate(data)
    except Exception as e:
        logger.warning("doctor_guardian_events failed: %s", e)
        return f"Doctor Guardian events query failed: {e}"

register(Skill(
    name="doctor_guardian_events", category="doctor",
    description="Get Guardian deployment events timeline from Doctor. Shows reconcile cycles, task completions, failures.",
    parameters={
        "partition": "Partition name filter (optional)",
        "limit": "Max events (default 20)",
    },
    status_prefix="Querying Guardian events",
    handler=_tool_doctor_guardian_events,
))

# ---------------------------------------------------------------------------

async def _tool_doctor_event_analysis(args: dict) -> str:
    partition = str(args.get("partition", "")).strip()
    tenant = str(args.get("tenant", "default")).strip() or "default"
    limit = _to_int(args.get("limit"), default=80, minimum=5, maximum=MAX_EVENT_ANALYSIS_LIMIT)
    params = [f"tenant={quote_plus(tenant)}", f"limit={limit}"]
    if partition:
        params.append(f"partition={quote_plus(partition)}")

    try:
        raw = await _doctor_get(f"/v1/guardian/events?{'&'.join(params)}")
    except Exception as e:
        logger.warning("doctor_event_analysis failed to fetch events: %s", e)
        return f"Doctor event analysis failed: {e}"

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return "Doctor returned non-JSON event payload. Raw sample:\n" + _truncate(raw, 2000)

    events = _extract_event_list(payload)
    return _summarize_guardian_events(events, requested_partition=partition or None)


register(Skill(
    name="doctor_event_analysis", category="doctor",
    description="Analyze Guardian deployment events from Doctor and return failure/success breakdown, partition impact, and prioritized failure signals.",
    parameters={
        "partition": "Optional partition name filter",
        "tenant": "Doctor tenant (default 'default')",
        "limit": f"How many events to analyze (default 80, max {MAX_EVENT_ANALYSIS_LIMIT})",
    },
    status_prefix="Analyzing Doctor Guardian events",
    handler=_tool_doctor_event_analysis,
))

# ---------------------------------------------------------------------------
# --- NIGHTWATCH TOOLS ---
# ---------------------------------------------------------------------------

async def _nightwatch_get(path: str, params: dict | None = None) -> str:
    if not settings.nightwatch_url:
        return "Nightwatch is not configured (NIGHTWATCH_URL is empty)."
    headers = {}
    if settings.nightwatch_proxy_token:
        headers["X-Nightwatch-Token"] = settings.nightwatch_proxy_token
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), headers=headers) as client:
            response = await client.get(
                f"{settings.nightwatch_url.rstrip('/')}{path}", params=params or {}
            )
            response.raise_for_status()
            return _truncate(response.text, 16000)
    except Exception as exc:
        logger.warning("nightwatch_get(%s) failed: %s", path, exc)
        return f"Nightwatch request failed: {exc}"


async def _tool_nightwatch_overview(_args: dict) -> str:
    return await _nightwatch_get("/v1/overview")

register(Skill(
    name="nightwatch_overview", category="nightwatch",
    description="Show Nightwatch shadow-mode status: enabled assignments, open/investigating incidents, handoffs, escalations, and last poll. Use when the user asks what the background SRE agent is doing.",
    parameters={},
    status_prefix="Reading Nightwatch overview",
    handler=_tool_nightwatch_overview,
))

async def _tool_nightwatch_incidents(args: dict) -> str:
    params = {}
    if args.get("status"):
        params["status"] = str(args["status"])
    if args.get("partition"):
        params["partition"] = str(args["partition"])
    params["limit"] = _to_int(args.get("limit"), default=20, minimum=1, maximum=100)
    return await _nightwatch_get("/v1/incidents", params)

register(Skill(
    name="nightwatch_incidents", category="nightwatch",
    description="List Nightwatch incidents. Each incident includes status, severity, root-cause candidate, confidence, handoff/escalation state, and recommended action (recommendation only in shadow mode).",
    parameters={
        "status": "Optional status filter (open, investigating, resolved, failed)",
        "partition": "Optional partition filter",
        "limit": "Max incidents (default 20, max 100)",
    },
    status_prefix="Listing Nightwatch incidents",
    handler=_tool_nightwatch_incidents,
))

async def _tool_nightwatch_incident_detail(args: dict) -> str:
    incident_id = str(args.get("incident_id", "")).strip()
    if not incident_id:
        return "Error: incident_id is required"
    return await _nightwatch_get(f"/v1/incidents/{quote_plus(incident_id)}")

register(Skill(
    name="nightwatch_incident_detail", category="nightwatch",
    description="Get a Nightwatch incident with alerts, timeline events, evidence, and findings. Use to explain why Nightwatch made a recommendation or escalated.",
    parameters={"incident_id": "Incident ID from nightwatch_incidents"},
    status_prefix="Reading Nightwatch incident",
    handler=_tool_nightwatch_incident_detail,
))

async def _tool_nightwatch_activity(args: dict) -> str:
    params = {}
    if args.get("since"):
        params["since"] = str(args["since"])
    if args.get("partition"):
        params["partition"] = str(args["partition"])
    params["limit"] = _to_int(args.get("limit"), default=50, minimum=1, maximum=200)
    return await _nightwatch_get("/v1/activity", params)

register(Skill(
    name="nightwatch_activity", category="nightwatch",
    description="Show the Nightwatch activity ledger: what the background agent observed, investigated, handed off, or escalated. Use when the user asks what happened while they were away.",
    parameters={
        "since": "Optional ISO timestamp lower bound",
        "partition": "Optional partition filter",
        "limit": "Max events (default 50, max 200)",
    },
    status_prefix="Reading Nightwatch activity",
    handler=_tool_nightwatch_activity,
))

# ---------------------------------------------------------------------------
# --- MONOFS TOOLS ---
# ---------------------------------------------------------------------------

async def _tool_monofs_search(args: dict) -> str:
    query = args.get("query", "")
    if not query:
        return "Error: query is required"
    max_results = int(args.get("max_results", 20))
    file_pattern = args.get("file_pattern", "")
    case_sensitive = args.get("case_sensitive", "").lower() in ("true", "1", "yes")
    use_regex = args.get("regex", "").lower() in ("true", "1", "yes")

    cmd = ["monofs-session", "search", "--query", query, "--max-results", str(max(1, min(max_results, 100)))]
    if file_pattern:
        cmd.extend(["--file-pattern", file_pattern])
    if case_sensitive:
        cmd.append("--case-sensitive")
    if use_regex:
        cmd.append("--regex")

    try:
        env = await _monofs_env()
        return _truncate(await _run_subprocess(cmd, timeout=MONOFS_SEARCH_TIMEOUT, env=env))
    except Exception as e:
        logger.warning("monofs_search failed: %s", e)
        return f"MonoFS search failed: {e}"

register(Skill(
    name="monofs_search", category="monofs",
    description="Search code across ALL repositories in the MonoFS virtual filesystem. Finds functions, classes, error strings, config patterns. Use to locate source code, configs, or error sources.",
    parameters={
        "query": "Text or regex to search for",
        "max_results": "Max results (default 20)",
        "file_pattern": "File glob filter (e.g. '*.go', '*.yaml')",
        "case_sensitive": "Case-sensitive search (true/false)",
        "regex": "Treat query as regex (true/false)",
    },
    status_prefix="Searching MonoFS codebase",
    handler=_tool_monofs_search,
))

# ---------------------------------------------------------------------------

async def _tool_monofs_status(_args: dict) -> str:
    try:
        env = await _monofs_env()
        return _truncate(await _run_subprocess(["monofs-session", "status"], timeout=10.0, env=env))
    except Exception as e:
        logger.warning("monofs_status failed: %s", e)
        return f"MonoFS status failed: {e}"

register(Skill(
    name="monofs_status", category="monofs",
    description="Show MonoFS write session status: pending changes, staged files, unpushed commits. Use to check local workspace state.",
    parameters={},
    status_prefix="Checking MonoFS session",
    handler=_tool_monofs_status,
))

# ---------------------------------------------------------------------------

async def _tool_monofs_refs(_args: dict) -> str:
    try:
        env = await _monofs_env()
        return _truncate(await _run_subprocess(["monofs-session", "refs"], timeout=10.0, env=env))
    except Exception as e:
        logger.warning("monofs_refs failed: %s", e)
        return f"MonoFS refs failed: {e}"

register(Skill(
    name="monofs_refs", category="monofs",
    description="Show tracked workspace refs for all mounted repositories. Lists all ingested repos with their current commit hashes.",
    parameters={},
    status_prefix="Querying MonoFS refs",
    handler=_tool_monofs_refs,
))


# ---------------------------------------------------------------------------
# --- REMOTE VS CODE HELPER ---
# ---------------------------------------------------------------------------

async def _tool_remote_vscode_plan(args: dict) -> str:
    method = str(args.get("method", "auto")).strip().lower() or "auto"
    host = str(args.get("host", "your-server")).strip() or "your-server"
    user = str(args.get("user", "your-user")).strip() or "your-user"
    port = _to_int(args.get("port"), default=8080, minimum=1, maximum=65535)
    workspace = str(args.get("workspace_dir", "~/workspace")).strip() or "~/workspace"

    if method == "auto":
        method = "ssh" if shutil.which("code") else "code-server"

    if method == "ssh":
        return "\n".join([
            "Remote VS Code plan (SSH mode):",
            "1. Ensure VS Code is installed locally on your laptop/workstation.",
            f"2. Verify SSH access: ssh {user}@{host}",
            "3. In VS Code, install extension: ms-vscode-remote.remote-ssh.",
            "4. Connect to host via 'Remote-SSH: Connect to Host...'.",
            f"5. Open workspace folder: {workspace}",
            "",
            "Server prep commands:",
            "sudo apt-get update && sudo apt-get install -y openssh-server",
            "sudo systemctl enable --now ssh",
        ])

    if method == "code_tunnel":
        return "\n".join([
            "Remote VS Code plan (code tunnel mode):",
            "1. Install VS Code CLI (`code`) on the target machine.",
            "2. Run `code tunnel` and authenticate once.",
            "3. From local VS Code, connect through tunnel.",
            "",
            "Commands on target host:",
            "code --version",
            "code tunnel",
            "",
            "This method avoids exposing SSH or HTTP ports publicly.",
        ])

    return "\n".join([
        "Remote VS Code plan (code-server mode):",
        f"1. Install code-server on target host {host}.",
        "2. Start code-server bound to localhost only.",
        "3. Use SSH port-forwarding from your local machine.",
        "",
        "Install + run commands on target host:",
        "curl -fsSL https://code-server.dev/install.sh | sh",
        f"code-server --bind-addr 127.0.0.1:{port} --auth password {workspace}",
        "",
        "Local machine tunnel command:",
        f"ssh -L {port}:127.0.0.1:{port} {user}@{host}",
        f"Then open http://127.0.0.1:{port} in browser.",
    ])


register(Skill(
    name="remote_vscode_plan", category="strata",
    description="Generate a concrete remote VS Code setup plan (SSH, code tunnel, or code-server) with commands and secure defaults.",
    parameters={
        "method": "auto|ssh|code_tunnel|code-server (default auto)",
        "host": "Target host name or IP",
        "user": "SSH user",
        "port": "Port for code-server mode (default 8080)",
        "workspace_dir": "Remote workspace path (default ~/workspace)",
    },
    status_prefix="Preparing remote VS Code setup plan",
    handler=_tool_remote_vscode_plan,
))

# ---------------------------------------------------------------------------

async def _tool_remote_vscode_install(args: dict) -> str:
    method = str(args.get("method", "code-server")).strip().lower() or "code-server"
    plan_only = _to_bool(args.get("plan_only"), default=True)
    timeout_seconds = _to_int(args.get("timeout_seconds"), default=900, minimum=30, maximum=3600)

    if method not in {"code-server", "ssh"}:
        return "Error: method must be one of: code-server, ssh"

    if method == "ssh":
        commands = [
            ["bash", "-lc", "sudo apt-get update && sudo apt-get install -y openssh-server && sudo systemctl enable --now ssh"],
            ["bash", "-lc", "sudo systemctl status ssh --no-pager | head -40"],
        ]
    else:
        commands = [
            ["bash", "-lc", "curl -fsSL https://code-server.dev/install.sh | sh"],
            ["code-server", "--version"],
        ]

    if plan_only:
        return "\n".join([
            "Remote VS Code install proposal generated. No commands executed.",
            "To execute, call remote_vscode_install with plan_only=false and confirm=true.",
            "",
            "Planned commands:",
            *[f"{idx}. {' '.join(cmd)}" for idx, cmd in enumerate(commands, 1)],
        ])

    outputs: list[str] = []
    for idx, cmd in enumerate(commands, 1):
        result = await _run_subprocess(cmd, timeout=float(timeout_seconds))
        outputs.append(f"Step {idx}: {' '.join(cmd)}\n{_truncate(result, 4000)}")
        if result.startswith("Error"):
            break
    return "\n\n".join(outputs)


register(Skill(
    name="remote_vscode_install", category="strata",
    description="Install remote VS Code dependencies (code-server or SSH server). Default mode is proposal-only.",
    parameters={
        "method": "code-server|ssh (default code-server)",
        "plan_only": "When true, return commands without executing (default true)",
        "timeout_seconds": "Per-command timeout in seconds (default 900)",
        "confirm": "Required for mutating tools. Set true/yes after user approval.",
    },
    status_prefix="Installing remote VS Code dependencies",
    mutating=True,
    handler=_tool_remote_vscode_install,
))


# ---------------------------------------------------------------------------
# --- WORKSPACE TOOLS (MonoFS writable mount, Steve's coding lane) ---
# ---------------------------------------------------------------------------

register(Skill(
    name="workspace_status", category="workspace",
    description="Show the MonoFS writable workspace state: mount path, router, session socket, pending changes, staged files, unpushed commits. Start here before editing code.",
    parameters={},
    status_prefix="Checking MonoFS workspace",
    handler=workspace_tools.workspace_status,
))

register(Skill(
    name="workspace_list", category="workspace",
    description="List files and directories in the MonoFS workspace. Paths are relative to the mount root (e.g. 'monofs/internal/fuse').",
    parameters={"path": "Directory path relative to the workspace root (default '.')"},
    status_prefix="Listing workspace files",
    handler=workspace_tools.workspace_list,
))

register(Skill(
    name="workspace_read", category="workspace",
    description="Read a text file from the MonoFS workspace. Use before editing to understand surrounding code.",
    parameters={
        "path": "File path relative to the workspace root",
        "max_bytes": "Optional read cap (default 120000)",
    },
    status_prefix="Reading workspace file",
    handler=workspace_tools.workspace_read,
))

register(Skill(
    name="workspace_write", category="workspace",
    description="Write a file in the MonoFS workspace (creates or overwrites). Edits are local overlay changes until committed. Provide the complete file content.",
    parameters={
        "path": "File path relative to the workspace root",
        "content": "Full file content to write",
        "create_dirs": "Create parent directories when missing (default true)",
    },
    status_prefix="Writing workspace file",
    handler=workspace_tools.workspace_write,
))

register(Skill(
    name="workspace_diff", category="workspace",
    description="Show unified diffs of local workspace changes. Run after editing to review the change surface before committing.",
    parameters={"path": "Optional path filter (file or directory)"},
    status_prefix="Computing workspace diff",
    handler=workspace_tools.workspace_diff,
))

register(Skill(
    name="workspace_session", category="workspace",
    description="Manage the MonoFS write session: start, add (stage paths), rm (stage deletes), discard (DESTRUCTIVE, confirm required), log (local commits), refs, status, branch_show/branch_create/branch_switch.",
    parameters={
        "action": "start | add | rm | discard | log | refs | status | branch_show | branch_create | branch_switch",
        "paths": "Comma-separated paths for add/rm",
        "name": "Branch name for branch_create/branch_switch",
    },
    status_prefix="Running MonoFS session command",
    handler=workspace_tools.workspace_session,
))

register(Skill(
    name="workspace_commit", category="workspace",
    description="Stage optional paths and create a MonoFS virtual commit from local changes. Requires explicit approval (confirm=true) unless autonomous push is enabled.",
    parameters={
        "message": "Commit message",
        "paths": "Optional comma-separated paths to stage first",
    },
    status_prefix="Committing workspace changes",
    mutating=True,
    auto_confirm_key="auto_push_enabled",
    handler=workspace_tools.workspace_commit,
))

register(Skill(
    name="workspace_push", category="workspace",
    description="Push pending MonoFS commits upstream (squashed per repo). Requires explicit approval (confirm=true) unless autonomous push is enabled.",
    parameters={},
    status_prefix="Pushing workspace changes",
    mutating=True,
    auto_confirm_key="auto_push_enabled",
    handler=workspace_tools.workspace_push,
))


# ---------------------------------------------------------------------------
# Execute tool (dispatcher)
# ---------------------------------------------------------------------------

async def execute_tool(
    name: str,
    args: dict | None = None,
    *,
    allowed: set[str] | None = None,
) -> str:
    args = args or {}
    if allowed is not None and name not in allowed:
        return (
            f"Error: tool '{name}' is not available to this persona. "
            f"Available tools: {', '.join(sorted(allowed))}"
        )
    skill = _skills.get(name)
    if skill is None:
        return f"Error: unknown tool '{name}'. Available tools: {', '.join(sorted(_skills.keys()))}"
    if skill.handler is None:
        return f"Error: tool '{name}' has no handler"
    auto_confirmed = bool(skill.auto_confirm_key) and runtime_bool(skill.auto_confirm_key)
    if skill.mutating and not auto_confirmed and not _is_mutation_confirmed(args):
        return (
            f"Error: tool '{name}' mutates system state.\n"
            "1) First provide a proposal/plan to the user.\n"
            "2) Run only after explicit approval by setting args.confirm=true."
        )
    try:
        return await skill.handler(args)
    except Exception as e:
        logger.exception("Tool '%s' handler raised exception", name)
        return f"Tool '{name}' execution failed: {e}"


# Build exports on module load
_rebuild_exports()
