import { useNavigate } from "react-router-dom";
import { useChatStore } from "../stores/chatStore";
import { Terminal, ArrowRight, MessageSquare } from "lucide-react";

export default function LandingPage() {
  const navigate = useNavigate();
  const { sessions, createSession } = useChatStore();

  const handleNewSession = async () => {
    const id = await createSession();
    navigate(`/chat/${id}`);
  };

  return (
    <div className="relative h-full overflow-y-auto bg-dark-900 px-6">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top,_rgba(59,130,246,0.18),transparent_30%),radial-gradient(circle_at_bottom_right,_rgba(147,51,234,0.16),transparent_25%)]" />
      <div className="relative z-10 mx-auto flex min-h-full max-w-2xl flex-col items-center justify-center py-12 text-center">
        <div className="mb-8 flex flex-col items-center justify-center gap-6 sm:flex-row">
          <video
            src="/otto.mp4"
            autoPlay
            loop
            muted
            playsInline
            className="h-52 w-52 rounded-3xl border border-dark-500/80 object-cover shadow-[0_0_0_1px_rgba(59,130,246,0.12),0_20px_80px_rgba(0,0,0,0.5)]"
          />
          <video
            src="/steve.mp4"
            autoPlay
            loop
            muted
            playsInline
            className="h-52 w-52 rounded-3xl border border-dark-500/80 object-cover shadow-[0_0_0_1px_rgba(147,51,234,0.12),0_20px_80px_rgba(0,0,0,0.5)]"
          />
        </div>

        <h1 className="mb-4 text-4xl font-bold tracking-tight text-gray-100 sm:text-5xl">
          Meet Otto &amp; Steve
        </h1>
        <p className="mb-10 max-w-lg text-sm text-gray-500">
          Otto keeps the infrastructure alive. Steve ships the code. They switch
          based on context — or pin one manually.
        </p>

        <button
          onClick={handleNewSession}
          className="inline-flex items-center gap-2 rounded-xl bg-accent-blue px-6 py-3 text-base font-medium text-white shadow-[0_10px_35px_rgba(59,130,246,0.35)] transition-transform hover:-translate-y-0.5 hover:bg-accent-blue/90"
        >
          <Terminal size={18} />
          Start a session
          <ArrowRight size={16} />
        </button>

        {sessions.length > 0 && (
          <div className="mt-12 w-full max-w-xl text-left">
            <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold uppercase tracking-[0.2em] text-gray-400">
              <MessageSquare size={14} />
              Recent sessions
            </h2>
            <div className="space-y-1.5">
              {sessions.slice(0, 5).map((s) => (
                <button
                  key={s.id}
                  onClick={() => navigate(`/chat/${s.id}`)}
                  className="flex w-full items-center gap-2 rounded-lg border border-dark-600 bg-dark-800/50 px-3 py-2 text-sm text-gray-400 transition-colors hover:border-dark-500 hover:bg-dark-700 hover:text-gray-200"
                >
                  <MessageSquare size={13} className="shrink-0" />
                  <span className="truncate">{s.title}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
