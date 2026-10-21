from __future__ import annotations

import asyncio
import logging
import re

from app.config import runtime_bool
from app.agent.personas import DEFAULT_PERSONA_ID, is_persona_id
from app.llm.client import chat_completion

logger = logging.getLogger(__name__)

_PERSONA_IDS = ("otto", "steve")

MENTION_RE = re.compile(r"(?<![\w@])@(otto|steve)\b", re.IGNORECASE)

_FOLLOWUP_STARTS = (
    "yes", "yeah", "yep", "yup", "ok", "okay", "sure", "no", "nope", "nah",
    "go ahead", "do it", "continue", "proceed", "run it", "ship it", "apply",
    "thanks", "thank you", "thx", "perfect", "great", "nice", "more", "again",
    "why", "how so", "and", "what about", "explain", "elaborate", "keep going",
)

_CLASSIFIER_SYSTEM_PROMPT = (
    "You route messages in a combined assistant that has two personas:\n"
    "- otto: site reliability engineering. Incidents, outages, alerts, monitoring, logs, "
    "metrics, traces, Kubernetes/infrastructure health, deployments, rollbacks, production state.\n"
    "- steve: software engineering. Writing/reading/reviewing code, debugging programs, "
    "implementing features, tests, commits, branches, diffs, libraries, APIs, data schemas.\n"
    "Decide who should answer the user's latest message. "
    "Reply with exactly one lowercase word: otto or steve. No punctuation, no explanation."
)

_OTTO_HINTS = (
    "incident", "outage", "alert", "alerting", "alerts", "grafana", "prometheus",
    "kubernetes", "k8s", "cluster", "namespace", "pod", "pods", "deployment",
    "deploy", "rollback", "reconcile", "drift", "guardian", "doctor", "telemetry",
    "monitoring", "metrics", "logs", "log", "traces", "latency", "uptime", "sla",
    "sre", "production", "5xx", "500s", "crashloop", "crash", "oom", "memory leak",
    "node", "nodes", "helm", "infrastructure", "availability", "downtime", "health check",
)

_STEVE_HINTS = (
    "implement", "refactor", "function", "method", "class", "bug", "bugfix", "fix",
    "code", "codebase", "repo", "repository", "commit", "branch", "diff", "merge",
    "pull request", "pr", "unit test", "tests", "test", "pytest", "compile",
    "build error", "stack trace", "traceback", "syntax", "lint", "typescript",
    "javascript", "python", "golang", "rust", "react", "sql", "schema", "migration",
    "api endpoint", "endpoint", "library", "dependency", "dependencies", "package",
    "write a", "add a", "create a", "feature", "variable", "loop", "algorithm",
)

_OTTO_HINT_RE = re.compile(r"\b(" + "|".join(re.escape(h) for h in _OTTO_HINTS) + r")\b", re.IGNORECASE)
_STEVE_HINT_RE = re.compile(r"\b(" + "|".join(re.escape(h) for h in _STEVE_HINTS) + r")\b", re.IGNORECASE)

MAX_CLASSIFIER_TIMEOUT = 6.0
MAX_CLASSIFIER_CHARS = 1200


def extract_mention(text: str) -> str | None:
    """Return "otto"/"steve" when the message explicitly @-mentions a persona."""
    match = MENTION_RE.search(text or "")
    if not match:
        return None
    return match.group(1).lower()


def strip_mention(text: str) -> str:
    """Remove persona @-mentions from the message text."""
    cleaned = MENTION_RE.sub("", text or "")
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def looks_like_followup(text: str) -> bool:
    """Heuristic: short/affirmative messages are continuations of the current thread."""
    cleaned = (text or "").strip().lower()
    if not cleaned:
        return True
    if len(cleaned.split()) <= 4:
        return True
    return cleaned.startswith(_FOLLOWUP_STARTS)


def keyword_route(text: str) -> str | None:
    """Cheap keyword vote; returns a persona id only on a clear signal."""
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    otto_score = len(_OTTO_HINT_RE.findall(cleaned))
    steve_score = len(_STEVE_HINT_RE.findall(cleaned))
    if otto_score == steve_score:
        return None
    return "otto" if otto_score > steve_score else "steve"


async def classify_persona(message: str) -> str | None:
    """Ask the local model to route the message; returns None when undecided."""
    snippet = (message or "").strip()[:MAX_CLASSIFIER_CHARS]
    if not snippet:
        return None
    try:
        result = await asyncio.wait_for(
            chat_completion(
                [
                    {"role": "system", "content": _CLASSIFIER_SYSTEM_PROMPT},
                    {"role": "user", "content": snippet},
                ],
                temperature=0.0,
                max_tokens=4,
                stream=False,
            ),
            timeout=MAX_CLASSIFIER_TIMEOUT,
        )
    except Exception as exc:
        logger.debug("Persona classifier failed: %s", exc)
        return None

    match = re.search(r"\b(otto|steve)\b", str(result or "").lower())
    if not match:
        return None
    return match.group(1)


async def resolve_persona(
    *,
    requested: str | None,
    active_persona: str | None,
    message: str,
) -> tuple[str, str]:
    """Resolve which persona answers.

    Returns (persona_id, reason) where reason is one of:
    manual | mention | keyword | sticky | classifier | default.
    """
    requested = (requested or "").strip().lower()
    if is_persona_id(requested):
        return requested, "manual"

    active = active_persona if is_persona_id(str(active_persona or "")) else None

    if requested != "auto" or not runtime_bool("auto_switch_enabled", default=True):
        return (active or DEFAULT_PERSONA_ID), "manual"

    mention = extract_mention(message)
    if mention:
        return mention, "mention"

    keyword = keyword_route(message)
    if keyword:
        return keyword, "keyword"

    if active:
        if looks_like_followup(message):
            return active, "sticky"
        classified = await classify_persona(message)
        if classified and classified != active:
            return classified, "classifier"
        return active, "sticky"

    classified = await classify_persona(message)
    if classified:
        return classified, "classifier"
    return DEFAULT_PERSONA_ID, "default"
