from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agent import session as session_mgr
from app.agent.loop import run_agent_turn
from app.agent.personas import engine_profile, resolve_engine
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["proposal"])

_proposal_tasks: set[asyncio.Task] = set()


class ImprovementInput(BaseModel):
    id: str = ""
    title: str
    category: str = "metrics"
    rationale: str = ""
    proposed_change: str = ""
    target: str = ""
    confidence: float = 0.0


class ProposalRequest(BaseModel):
    incident_id: str
    incident_title: str = ""
    partition: str = ""
    severity: str = "warning"
    summary: str = ""
    alertname: str = ""
    labels: dict[str, str] = Field(default_factory=dict)
    improvement: ImprovementInput


def _prompt(req: ProposalRequest) -> str:
    labels = ", ".join(f"{key}={value}" for key, value in sorted(req.labels.items()))
    improvement = req.improvement
    return (
        "Nightwatch requested an observability improvement diff proposal. "
        "This is a proposal-first task: produce a reviewable change, do not commit or push.\n\n"
        f"Incident: {req.incident_title or req.incident_id}\n"
        f"Partition: {req.partition or 'unknown'}\n"
        f"Severity: {req.severity}\n"
        f"Alert: {req.alertname or 'unknown'}"
        + (f" ({labels})" if labels else "")
        + "\n"
        f"Summary: {req.summary or 'no summary'}\n\n"
        f"Improvement proposal: {improvement.title}\n"
        f"Category: {improvement.category}\n"
        f"Rationale: {improvement.rationale}\n"
        f"Proposed change: {improvement.proposed_change}\n"
        f"Target: {improvement.target or 'infer from the workspace'}\n\n"
        "Do this in the MonoFS workspace:\n"
        "1. Call workspace_status to confirm the mount is writable.\n"
        "2. Use monofs_search and workspace_read to locate the relevant code, manifest, or dashboard.\n"
        "3. Make the smallest safe change with workspace_write. If the correct change is ambiguous "
        "or the workspace is unavailable, do not invent it; return an exact implementation plan "
        "with file paths and snippets instead.\n"
        "4. Call workspace_diff and include the unified diff in your answer.\n"
        "5. Do not call workspace_commit or workspace_push.\n\n"
        "Answer with: the files changed, the diff, why this closes the observability gap, and "
        "what verification command the reviewer should run."
    )


async def _mark_handoff(incident_id: str, improvement_id: str, session_id: str) -> None:
    if not settings.nightwatch_url or not improvement_id:
        return
    url = (
        f"{settings.nightwatch_url.rstrip('/')}/v1/incidents/{incident_id}"
        f"/improvements/{improvement_id}/session"
    )
    headers = {}
    if settings.nightwatch_proxy_token:
        headers["X-Nightwatch-Token"] = settings.nightwatch_proxy_token
    try:
        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            await client.post(url, json={"session_id": session_id})
    except Exception as exc:  # pragma: no cover - network failure path
        logger.warning("Nightwatch improvement handoff update failed: %s", exc)


async def _run_proposal(session_id: str, prompt: str) -> None:
    engine = await resolve_engine("steve")
    profile = engine_profile(engine.id)
    try:
        await run_agent_turn(
            prompt,
            session_id,
            stream=False,
            persona_id="steve",
            profile=profile,
        )
    except Exception as exc:  # pragma: no cover - defensive background path
        logger.exception("Nightwatch proposal failed for session %s", session_id)
        try:
            await session_mgr.add_message(
                session_id,
                "assistant",
                f"Proposal failed: {exc}",
                persona="steve",
            )
        except Exception:
            logger.exception("Could not persist proposal failure for session %s", session_id)


@router.post("/api/agent/proposal")
async def create_proposal(req: ProposalRequest) -> dict[str, Any]:
    improvement = req.improvement
    if not improvement.title.strip():
        raise HTTPException(status_code=400, detail="improvement.title is required")
    if not improvement.proposed_change.strip():
        raise HTTPException(status_code=400, detail="improvement.proposed_change is required")
    if len(improvement.proposed_change) > 8000:
        raise HTTPException(status_code=400, detail="improvement.proposed_change is too long")

    title = f"Nightwatch proposal · {req.incident_title or req.incident_id}"
    session = await session_mgr.create_session(title=title[:120], persona="steve")
    session_id = session["id"]
    prompt = _prompt(req)
    # Mark the Nightwatch record immediately; the Steve turn runs in the
    # background so the UI can open the session without waiting for the LLM.
    await _mark_handoff(req.incident_id, improvement.id, session_id)
    task = asyncio.create_task(_run_proposal(session_id, prompt))
    _proposal_tasks.add(task)
    task.add_done_callback(_proposal_tasks.discard)
    return {"session_id": session_id, "status": "started"}
