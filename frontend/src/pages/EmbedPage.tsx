import { useEffect, useMemo, useRef } from "react";
import { ExternalLink, Sparkles } from "lucide-react";
import { useChatStore } from "../stores/chatStore";
import ChatMessages from "../components/chat/ChatMessages";
import ChatInput from "../components/chat/ChatInput";

interface ViewContext {
  label: string;
  product: string;
  path: string;
}

function readViewContext(): ViewContext {
  const params = new URLSearchParams(window.location.search);
  const label = params.get("context") || "Strata platform";
  const separator = label.indexOf("·");
  if (separator > 0) {
    return {
      label,
      product: label.slice(0, separator).trim(),
      path: label.slice(separator + 1).trim() || "/",
    };
  }
  return { label, product: "strata", path: "/" };
}

export default function EmbedPage() {
  const {
    init,
    cleanup,
    activeSessionId,
    createSession,
    sendMessage,
    ws,
    agentConnected,
  } = useChatStore();
  const context = useMemo(readViewContext, []);
  const autoPromptSent = useRef(false);

  useEffect(() => {
    init();
    return () => cleanup();
  }, [cleanup, init]);

  useEffect(() => {
    if (!activeSessionId) {
      createSession().catch(() => {});
    }
  }, [activeSessionId, createSession]);

  // Ask the agent about the view as soon as the embed session is ready. The
  // context is part of the user message so it stays in the conversation
  // history for later turns.
  useEffect(() => {
    if (
      autoPromptSent.current ||
      !activeSessionId ||
      !ws ||
      !agentConnected
    ) {
      return;
    }
    const args = JSON.stringify({
      product: context.product,
      path: context.path,
    });
    const prompt =
      `View context: ${context.label}\n\n` +
      `First call:\n{"tool": "strata_view_context", "args": ${args}}\n\n` +
      "Then answer: what this view shows, what to check first, and the live " +
      "evidence you actually queried. Keep it concise. Do not invent data.";
    if (sendMessage(prompt)) {
      autoPromptSent.current = true;
    }
  }, [activeSessionId, ws, agentConnected, sendMessage, context]);

  return (
    <main className="flex h-screen flex-col bg-dark-900">
      <header className="flex shrink-0 items-center gap-3 border-b border-dark-600 bg-dark-800/90 px-4 py-3">
        <span className="flex h-8 w-8 items-center justify-center rounded-full border border-accent-purple/40 bg-accent-purple/15 text-accent-purple">
          <Sparkles size={15} />
        </span>
        <div className="min-w-0">
          <h1 className="text-sm font-semibold text-gray-100">Ask Strata</h1>
          <div className="truncate text-[11px] text-gray-500">{context.label}</div>
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
