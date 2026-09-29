import { useEffect, useRef } from "react";
import { useChatStore } from "../../stores/chatStore";
import MessageBubble from "./MessageBubble";
import AgentAvatar from "./AgentAvatar";
import { Sparkles } from "lucide-react";

export default function ChatMessages() {
  const { messages, isStreaming, streamContent } = useChatStore();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamContent]);

  // Empty state
  if (messages.length === 0 && !isStreaming) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-6 p-8">
        <AgentAvatar size="lg" />
        <div className="text-center">
          <h2 className="text-xl font-semibold text-gray-200 mb-2">
            Welcome to Outage Otto
          </h2>
          <p className="text-sm text-gray-500 max-w-md">
            Sarcastic on the surface, ruthlessly helpful underneath. Your local
            AI SRE powered by Gemma 4 — private, fast, and always on call.
          </p>
        </div>
        <div className="flex flex-wrap gap-2 justify-center max-w-lg">
          {[
            "Explain quantum computing simply",
            "Write a Python fizzbuzz",
            "What's the weather like on Mars?",
            "Help me debug my code",
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
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  );
}
