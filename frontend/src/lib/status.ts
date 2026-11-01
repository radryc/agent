export type StatusTone = "healthy" | "warning" | "critical" | "unknown" | "pending";

export function statusTone(value: string | undefined | null): StatusTone {
  const normalized = (value ?? "").toLowerCase();
  if (normalized.includes("fail") || normalized.includes("crit") || normalized.includes("error") || normalized.includes("unhealthy")) {
    return "critical";
  }
  if (normalized.includes("attention") || normalized.includes("degrad") || normalized.includes("drift") || normalized.includes("warn")) {
    return "warning";
  }
  if (normalized.includes("pending") || normalized.includes("progress") || normalized.includes("queued") || normalized.includes("applying")) {
    return "pending";
  }
  if (normalized.includes("healthy") || normalized.includes("ok") || normalized.includes("stable") || normalized.includes("nominal")) {
    return "healthy";
  }
  return "unknown";
}

const LABELS: Record<StatusTone, string> = {
  healthy: "Healthy",
  warning: "Warning",
  critical: "Critical",
  pending: "Pending",
  unknown: "Unknown",
};

const TOKENS: Record<StatusTone, string> = {
  healthy: "--strata-healthy",
  warning: "--strata-warning",
  critical: "--strata-critical",
  pending: "--strata-pending",
  unknown: "--strata-unknown",
};

export function statusLabel(value: string | undefined | null): string {
  return LABELS[statusTone(value)];
}

export function statusToken(value: string | undefined | null): string {
  return TOKENS[statusTone(value)];
}
