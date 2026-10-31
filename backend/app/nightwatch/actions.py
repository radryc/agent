"""Nightwatch action catalog.

Shadow mode can *recommend* every action below, but no executor is wired.
Assist mode will add the policy engine and Action Gateway; auto mode will
require the additional verification/rollback/escalation controls.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActionSpec:
    name: str
    risk: str
    description: str


ACTION_CATALOG: dict[str, ActionSpec] = {
    "none": ActionSpec("none", "L0", "No action; continue observing."),
    "notify": ActionSpec("notify", "L1", "Send a notification without changing infrastructure."),
    "ack": ActionSpec("ack", "L1", "Acknowledge the Sentinel alert (assist only)."),
    "silence": ActionSpec("silence", "L1", "Create a time-boxed Sentinel silence (assist only)."),
    "escalate": ActionSpec("escalate", "L1", "Escalate to the partition owner/platform on-call."),
    "handoff": ActionSpec("handoff", "L1", "Hand a cross-partition finding to the owning team."),
    "reconcile": ActionSpec("reconcile", "L2", "Ask Guardian to reconcile the affected intent."),
    "restart": ActionSpec("restart", "L3", "Restart the affected workload (assist/auto only)."),
    "scale": ActionSpec("scale", "L3", "Scale the affected workload (assist/auto only)."),
    "rollback": ActionSpec("rollback", "L3", "Roll back to the previous release (assist/auto only)."),
}

ALLOWED_RECOMMENDATIONS = set(ACTION_CATALOG)
