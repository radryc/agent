import { useState, useRef, useEffect } from "react";
import { useChatStore } from "../../stores/chatStore";
import { Send } from "lucide-react";

export default function ChatInput() {
  const [text, setText] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const { isStreaming, sendMessage } = useChatStore();

  const adjustHeight = () => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = Math.min(el.scrollHeight, 128) + "px";
    }
  };

  useEffect(() => adjustHeight(), [text]);

  const handleSend = () => {
    if (!text.trim() || isStreaming) return;
    sendMessage(text);
    setText("");
    textareaRef.current?.focus();
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="border-t border-dark-600 bg-dark-800/80 backdrop-blur-sm px-4 py-3">
      <div className="flex items-end gap-2 max-w-4xl mx-auto">
        <div className="flex-1 relative">
          <textarea
            ref={textareaRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type a message..."
            rows={1}
            disabled={isStreaming}
            className="w-full resize-none rounded-xl border border-dark-500 bg-dark-700 px-4 py-3 text-sm text-gray-100 placeholder-gray-500 focus:border-accent-blue focus:outline-none focus:ring-1 focus:ring-accent-blue/50 disabled:opacity-50 transition-colors"
          />
          {text.length > 0 && (
            <span className="absolute bottom-1 right-3 text-[10px] text-gray-600">
              {text.length}
            </span>
          )}
        </div>

        <button
          onClick={handleSend}
          disabled={isStreaming || !text.trim()}
          className="shrink-0 rounded-xl bg-accent-blue p-3 text-white hover:bg-accent-blue/80 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        >
          <Send size={18} />
        </button>
      </div>

      <p className="text-center text-[10px] text-gray-600 mt-2">
        Lagent runs locally via llama.cpp — your data never leaves your machine
      </p>
    </div>
  );
}
