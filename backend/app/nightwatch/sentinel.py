from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable


def _as_label_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _as_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def _service_name(labels: dict[str, str], partition: str) -> str:
    if labels.get("service"):
        return labels["service"]
    if labels.get("guardian_service_full"):
        return labels["guardian_service_full"]
    intent = labels.get("guardian_intent")
    asset = labels.get("guardian_asset")
    if intent and asset:
        return f"{partition}.{intent}.{asset}"
    if asset:
        return f"{partition}.{asset}"
    return ""


def normalize_alert(raw: dict[str, Any], default_partition: str = "", default_tenant: str = "default") -> dict:
    """Normalize a Sentinel alert into the subset Nightwatch needs.

    Sentinel already emits canonical JSON; this function only tolerates minor
    legacy/camelCase differences and fills in derived service names.
    """
    labels = _as_label_map(raw.get("labels") or raw.get("Labels"))
    annotations = _as_label_map(raw.get("annotations") or raw.get("Annotations"))
    partition = str(raw.get("partition") or raw.get("Partition") or labels.get("partition") or default_partition)
    tenant = str(raw.get("tenant") or raw.get("Tenant") or default_tenant)
    alertname = str(
        raw.get("alertname")
        or raw.get("alertName")
        or labels.get("alertname")
        or annotations.get("alertname")
        or "alert"
    )
    severity = str(raw.get("severity") or raw.get("Severity") or labels.get("severity") or "warning")
    status = str(raw.get("status") or raw.get("Status") or "firing")
    fingerprint = str(raw.get("fingerprint") or raw.get("Fingerprint") or "")
    if not fingerprint:
        # Local fallback only. Sentinel normally always supplies a fingerprint.
        fingerprint = f"{partition}:{alertname}:{_stable_labels(labels)}"

    target_partition = str(
        labels.get("guardian_target_partition") or labels.get("target_partition") or ""
    ).strip()
    target_service = str(
        labels.get("guardian_target_service") or labels.get("target_service") or ""
    ).strip()
    if not target_service:
        target_intent = labels.get("guardian_target_intent")
        target_asset = labels.get("guardian_target_asset")
        if target_partition and target_intent and target_asset:
            target_service = f"{target_partition}.{target_intent}.{target_asset}"

    return {
        "fingerprint": fingerprint,
        "alertname": alertname,
        "partition": partition,
        "tenant": tenant,
        "service": _service_name(labels, partition),
        "target_partition": target_partition,
        "target_service": target_service,
        "status": status,
        "severity": severity,
        "labels": labels,
        "annotations": annotations,
        "starts_at": _as_dt(raw.get("startsAt") or raw.get("starts_at")),
        "ends_at": _as_dt(raw.get("endsAt") or raw.get("ends_at")),
        "source": str(raw.get("source") or raw.get("Source") or "sentinel"),
        "acked": bool(raw.get("acked") or raw.get("Acked") or False),
        "value": str(raw.get("value") or ""),
    }


def normalize_alerts(
    alerts: Iterable[dict[str, Any]],
    *,
    default_partition: str = "",
    default_tenant: str = "default",
) -> list[dict]:
    return [normalize_alert(alert, default_partition, default_tenant) for alert in alerts if isinstance(alert, dict)]


def correlation_key(alert: dict[str, Any]) -> str:
    partition = str(alert.get("partition") or "")
    service = str(alert.get("service") or "").strip()
    if service:
        return f"{partition}/{service}"
    return f"{partition}/{alert.get('alertname') or 'alert'}"


def _stable_labels(labels: dict[str, str]) -> str:
    return ",".join(f"{key}={labels[key]}" for key in sorted(labels) if key not in {"__name__", "value"})
