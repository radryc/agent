import { useEffect, useRef } from "react";
import { useChatStore } from "../../stores/chatStore";
import MessageBubble from "./MessageBubble";
import AgentAvatar from "./AgentAvatar";
import { Sparkles } from "lucide-react";

export default function ChatMessages() {
  const { messages, isStreaming, streamContent, streamPersona, activePersona } = useChatStore();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamContent]);

  const emptyAvatar = activePersona === "steve" ? "steve" : "otto";
  const streamingName = streamPersona === "steve" ? "Software Steve" : "Outage Otto";

  // Empty state
  if (messages.length === 0 && !isStreaming) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-6 p-8">
        <AgentAvatar size="lg" persona={emptyAvatar} />
        <div className="text-center">
          <h2 className="text-xl font-semibold text-gray-200 mb-2">
            Welcome to Otto &amp; Steve
          </h2>
          <p className="text-sm text-gray-500 max-w-md">
            Otto watches the infrastructure. Steve ships the code. Ask anything —
            the right persona picks up based on context, or pin one from the sidebar.
          </p>
        </div>
        <div className="flex flex-wrap gap-2 justify-center max-w-lg">
          {[
            "Is everything up?",
            "Fix the failing unit test",
            "What's the API latency trend?",
            "Refactor this function with tests",
          ].map((prompt) => (
            <button
              key={prompt}
              onClick={() => useChatStore.getState().sendMessage(prompt)}
              className="flex items-center gap-1.5 rounded-full border border-dark-500 px-3 py-1.5 text-xs text-gray-400 hover:bg-dark-700 hover:text-gray-200 transition-colors"
            >
              <Sparkles size={12} />
              {prompt}
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto px-4 py-6 space-y-6">
      {messages.map((msg) => (
        <MessageBubble key={msg.id} message={msg} />
      ))}

      {/* Streaming message */}
      {isStreaming && (
        <MessageBubble
          message={{
            id: "streaming",
            session_id: "",
            role: "assistant",
            content: "",
            created_at: "",
          }}
          isStreaming
          streamContent={streamContent}
          persona={streamPersona ?? undefined}
        />
      )}

      {/* Typing indicator when streaming just started */}
      {isStreaming && !streamContent && (
        <div className="flex items-center gap-3 pl-11">
          <div className="flex gap-1 text-gray-500">
            <span className="typing-dot w-1.5 h-1.5 rounded-full bg-gray-500" />
            <span className="typing-dot w-1.5 h-1.5 rounded-full bg-gray-500" />
            <span className="typing-dot w-1.5 h-1.5 rounded-full bg-gray-500" />
          </div>
          {streamPersona && (
            <span className="text-xs text-gray-500">{streamingName} is on it…</span>
          )}
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  );
}
