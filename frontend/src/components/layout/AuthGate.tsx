import { useEffect, useState } from "react";
import { ShieldCheck, LogIn, Loader2, AlertTriangle } from "lucide-react";
import { api, type AuthStatus } from "../../api/client";
import AgentAvatar from "../chat/AgentAvatar";

const SKIP_KEY = "lagent_skip_sso";

/**
 * Requires an interactive SSO login before the chat UI is usable.
 *
 * The backend always has a machine identity for Guardian/MonoFS calls, but
 * Doctor log/trace visibility is per-user. Requiring the browser login at the
 * front door means a new session starts with the same identity as Guardian,
 * MonoFS, and the rest of the platform.
 */
export default function AuthGate({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [error, setError] = useState("");
  const [skip, setSkip] = useState(() => sessionStorage.getItem(SKIP_KEY) === "1");

  useEffect(() => {
    let cancelled = false;
    api
      .authStatus()
      .then((value) => {
        if (!cancelled) setStatus(value);
      })
      .catch(() => {
        if (!cancelled) setError("Cannot reach the backend auth endpoint.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (status?.authenticated_as_user || skip) {
    return <>{children}</>;
  }

  if (!status && !error) {
    return (
      <div className="flex h-screen items-center justify-center bg-dark-900 text-gray-400">
        <Loader2 className="mr-2 animate-spin" size={18} />
        Checking SSO session…
      </div>
    );
  }

  const signIn = () => {
    const next = window.location.pathname + window.location.search;
    window.location.href = `/api/auth/login?next=${encodeURIComponent(next)}`;
  };

  const skipForNow = () => {
    sessionStorage.setItem(SKIP_KEY, "1");
    setSkip(true);
  };

  return (
    <div className="relative flex h-screen items-center justify-center overflow-hidden bg-dark-900 px-6">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top,_rgba(59,130,246,0.16),transparent_35%),radial-gradient(circle_at_bottom_right,_rgba(147,51,234,0.14),transparent_30%)]" />
      <div className="relative z-10 w-full max-w-md rounded-2xl border border-dark-500 bg-dark-800/90 p-8 shadow-[0_24px_80px_rgba(0,0,0,0.55)] backdrop-blur">
        <div className="mb-6 flex items-center gap-4">
          <AgentAvatar size="lg" still persona="otto" />
          <div>
            <h1 className="text-xl font-semibold text-gray-100">Sign in to Strata</h1>
            <p className="mt-1 text-xs text-gray-500">
              Outage Otto uses your platform identity for Guardian, MonoFS, and
              Doctor telemetry.
            </p>
          </div>
        </div>

        <div className="mb-6 rounded-lg border border-dark-600 bg-dark-700/60 p-3 text-xs text-gray-400">
          <div className="flex items-start gap-2">
            <ShieldCheck size={14} className="mt-0.5 shrink-0 text-emerald-400" />
            <span>
              Signing in applies the same access policy as Guardian and MonoFS.
              Logs, traces, and partitions are scoped to your user; metrics stay
              visible platform-wide.
            </span>
          </div>
        </div>

        {error && (
          <div className="mb-4 flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-300">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />
            {error}
          </div>
        )}

        {status && !status.browser_login_available && (
          <div className="mb-4 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-300">
            Browser login is not configured. Set the OIDC issuer, token URL, client
            ID, and redirect URL in Settings.
          </div>
        )}

        <button
          onClick={signIn}
          disabled={!status?.browser_login_available}
          className="flex w-full items-center justify-center gap-2 rounded-xl bg-accent-blue px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-blue-600 disabled:cursor-not-allowed disabled:opacity-40"
        >
          <LogIn size={16} />
          Sign in with SSO
        </button>

        <button
          onClick={skipForNow}
          className="mt-3 w-full text-center text-[11px] text-gray-600 transition-colors hover:text-gray-400"
        >
          Continue with the machine identity (limited telemetry visibility)
        </button>

        <p className="mt-5 text-center text-[10px] text-gray-700">
          Same identity provider as Guardian, MonoFS, and Doctor.
        </p>
      </div>
    </div>
  );
}
