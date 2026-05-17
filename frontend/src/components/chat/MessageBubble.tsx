import { Copy, Check } from "lucide-react";
import { useState } from "react";
import AgentAvatar from "./AgentAvatar";
import type { Message } from "../../types";

interface MessageBubbleProps {
  message: Message;
  isStreaming?: boolean;
  streamContent?: string;
}

// Regex to match markdown image syntax: ![alt](url)
const IMG_RE = /!\[([^\]]*)\]\((https?:\/\/[^)]+)\)/g;

function renderContent(text: string, isStreaming: boolean) {
  const parts: React.ReactNode[] = [];
  let last = 0;
  let match: RegExpExecArray | null;
  IMG_RE.lastIndex = 0;

  while ((match = IMG_RE.exec(text)) !== null) {
    if (match.index > last) {
      parts.push(
        <span key={last} className="whitespace-pre-wrap break-words">
          {text.slice(last, match.index)}
        </span>
      );
    }
    parts.push(
      <img
        key={match.index}
        src={match[2]}
        alt={match[1] || "generated image"}
        className="mt-2 mb-1 max-w-full rounded-lg border border-dark-600 cursor-pointer hover:opacity-90 transition-opacity"
        onClick={() => window.open(match![2], "_blank")}
      />
    );
    last = match.index + match[0].length;
  }

  if (last < text.length) {
    parts.push(
      <span key={last} className="whitespace-pre-wrap break-words">
        {text.slice(last)}
      </span>
    );
  }

  return (
    <>
      {parts}
      {isStreaming && (
        <span className="inline-block w-2 h-4 ml-0.5 bg-accent-blue animate-blink align-middle" />
      )}
    </>
  );
}

export default function MessageBubble({
  message,
  isStreaming = false,
  streamContent,
}: MessageBubbleProps) {
  const [copied, setCopied] = useState(false);
  const isUser = message.role === "user";
  const content = isStreaming ? streamContent || "" : message.content;

  const handleCopy = async () => {
    await navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div
      className={`flex gap-3 animate-fade-in ${
        isUser ? "flex-row-reverse" : "flex-row"
      }`}
    >
      {/* Avatar */}
      {!isUser && (
        <div className="shrink-0 mt-1">
          <AgentAvatar size="sm" isTyping={isStreaming} />
        </div>
      )}

      {/* Bubble */}
      <div
        className={`group relative max-w-[75%] rounded-2xl px-4 py-3 ${
          isUser
            ? "bg-accent-blue/20 border border-accent-blue/30 text-gray-100"
            : "bg-dark-700 border border-dark-600 text-gray-200"
        }`}
      >
        {/* Content with image rendering */}
        <div className="text-sm leading-relaxed">
          {renderContent(content, isStreaming)}
        </div>

        {/* Copy button */}
        {!isStreaming && content && (
          <button
            onClick={handleCopy}
            className="absolute -bottom-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity rounded-md bg-dark-600 p-1 hover:bg-dark-500"
            title="Copy"
          >
            {copied ? <Check size={12} className="text-green-400" /> : <Copy size={12} />}
          </button>
        )}

        {/* Timestamp */}
        {!isStreaming && message.created_at && (
          <div className="absolute -bottom-5 right-0 opacity-0 group-hover:opacity-100 transition-opacity text-[10px] text-gray-600">
            {new Date(message.created_at).toLocaleTimeString()}
          </div>
        )}
      </div>
    </div>
  );
}

