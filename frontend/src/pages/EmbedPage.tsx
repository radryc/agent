import { useEffect } from "react";
import { ExternalLink, Sparkles } from "lucide-react";
import { useChatStore } from "../stores/chatStore";
import ChatMessages from "../components/chat/ChatMessages";
import ChatInput from "../components/chat/ChatInput";

function contextLabel(): string {
  const params = new URLSearchParams(window.location.search);
  return params.get("context") || "Strata platform";
}

export default function EmbedPage() {
  const { init, cleanup, activeSessionId, createSession } = useChatStore();

  useEffect(() => {
    init();
    return () => cleanup();
  }, [cleanup, init]);

  useEffect(() => {
    if (!activeSessionId) {
      createSession().catch(() => {});
    }
  }, [activeSessionId, createSession]);

  return (
    <main className="flex h-screen flex-col bg-dark-900">
      <header className="flex shrink-0 items-center gap-3 border-b border-dark-600 bg-dark-800/90 px-4 py-3">
        <span className="flex h-8 w-8 items-center justify-center rounded-full border border-accent-purple/40 bg-accent-purple/15 text-accent-purple">
          <Sparkles size={15} />
        </span>
        <div className="min-w-0">
          <h1 className="text-sm font-semibold text-gray-100">Ask Strata</h1>
          <div className="truncate text-[11px] text-gray-500">{contextLabel()}</div>
        </div>
        <a
          href="/chat"
          target="_blank"
          rel="noreferrer"
          className="ml-auto inline-flex items-center gap-1 rounded-lg border border-dark-600 px-2.5 py-1.5 text-xs text-gray-400 hover:text-gray-100"
        >
          Open workspace <ExternalLink size={12} />
        </a>
      </header>
      <div className="flex min-h-0 flex-1 flex-col">
        <ChatMessages />
        <ChatInput />
      </div>
    </main>
  );
}
