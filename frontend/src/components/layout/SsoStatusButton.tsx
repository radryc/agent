import { useCallback, useEffect, useState } from "react";
import { LogIn, ShieldCheck, ShieldAlert, Loader2 } from "lucide-react";
import { api, type AuthStatus } from "../../api/client";

/**
 * Compact SSO state for the chat header.
 *
 * The backend always has a machine identity (client credentials) for
 * Guardian/Doctor calls. That is not the same as a signed-in user: per-user
 * telemetry visibility requires the browser login.
 */
export default function SsoStatusButton() {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api
      .authStatus()
      .then(setStatus)
      .catch(() => setError("Auth status unavailable"));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const signIn = () => {
    const next = window.location.pathname + window.location.search;
    window.location.href = `/api/auth/login?next=${encodeURIComponent(next)}`;
  };

  if (error) {
    return (
      <span
        className="flex items-center gap-1.5 text-[10px] text-red-400"
        title={error}
      >
        <ShieldAlert size={11} />
        SSO error
      </span>
    );
  }

  if (!status) {
    return (
      <span className="flex items-center gap-1.5 text-[10px] text-gray-600">
        <Loader2 size={11} className="animate-spin" />
        SSO
      </span>
    );
  }

  if (status.authenticated_as_user) {
    return (
      <span
        className="flex items-center gap-1.5 text-[10px] text-emerald-400"
        title={`Signed in to ${status.issuer || "SSO"} as ${status.subject || "user"}`}
      >
        <ShieldCheck size={11} />
        {status.subject || "SSO user"}
      </span>
    );
  }

  return (
    <button
      onClick={signIn}
      className="flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-[10px] text-amber-400 transition-colors hover:bg-dark-700 hover:text-amber-300"
      title="Sign in with the same SSO identity used by Guardian and MonoFS."
    >
      <LogIn size={11} />
      Sign in with SSO
    </button>
  );
}
