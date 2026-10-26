import { Copy, Check } from "lucide-react";
import { useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import AgentAvatar, { type AvatarPersona } from "./AgentAvatar";
import type { Message } from "../../types";

interface MessageBubbleProps {
  message: Message;
  isStreaming?: boolean;
  streamContent?: string;
  /** Override the persona that authored this message. */
  persona?: AvatarPersona;
}

const PERSONA_NAMES: Record<AvatarPersona, string> = {
  otto: "Outage Otto",
  steve: "Software Steve",
};

const PERSONA_BADGES: Record<AvatarPersona, string> = {
  otto: "border-blue-500/40 bg-blue-500/15 text-blue-300",
  steve: "border-purple-500/40 bg-purple-500/15 text-purple-300",
};

const VIDEO_RE = /\.(mp4|webm|mov|mkv|avi|m4v)(\?|$)/i;
const AUDIO_RE = /\.(mp3|wav|flac|ogg|m4a|aac)(\?|$)/i;

const markdownComponents: Components = {
  a: ({ href, children }) => {
    const url = href ?? "";
    if (VIDEO_RE.test(url)) {
      return (
        <video
          src={url}
          controls
          className="my-2 max-w-full rounded-lg border border-dark-600"
        />
      );
    }
    if (AUDIO_RE.test(url)) {
      return <audio src={url} controls className="my-2 w-full" />;
    }
    return (
      <a
        href={url}
        target="_blank"
        rel="noreferrer"
        className="text-accent-blue underline break-all hover:text-blue-300"
      >
        {children}
      </a>
    );
  },
  img: ({ src, alt }) => (
    <img
      src={typeof src === "string" ? src : ""}
      alt={alt || "generated image"}
      className="my-2 max-w-full cursor-pointer rounded-lg border border-dark-600 transition-opacity hover:opacity-90"
      onClick={() => {
        if (typeof src === "string") window.open(src, "_blank");
      }}
    />
  ),
};

export default function MessageBubble({
  message,
  isStreaming = false,
  streamContent,
  persona,
}: MessageBubbleProps) {
  const [copied, setCopied] = useState(false);
  const isUser = message.role === "user";
  const content = isStreaming ? streamContent || "" : message.content;
  const activePersona: AvatarPersona = persona ?? message.persona ?? "otto";

  const handleCopy = async () => {
    await navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (isUser) {
    return (
      <div className="flex animate-fade-in flex-row-reverse items-start gap-3">
        <div className="hidden h-8 w-8 shrink-0 items-center justify-center rounded-full border border-blue-400/40 bg-blue-500/20 text-[11px] font-semibold text-blue-200 sm:flex">
          You
        </div>
        <div className="group relative max-w-[85%] rounded-2xl rounded-tr-sm border border-blue-400/40 bg-blue-600/25 px-4 py-3 text-gray-50 shadow-[0_8px_30px_rgba(37,99,235,0.12)] sm:max-w-[75%]">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-[0.14em] text-blue-300">
            You
          </div>
          <div className="whitespace-pre-wrap break-words text-sm leading-relaxed">
            {content}
          </div>
          {content && (
            <button
              onClick={handleCopy}
              className="absolute -bottom-2 right-2 rounded-md bg-dark-600 p-1 opacity-0 transition-opacity hover:bg-dark-500 group-hover:opacity-100"
              title="Copy"
            >
              {copied ? (
                <Check size={12} className="text-green-400" />
              ) : (
                <Copy size={12} />
              )}
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex animate-fade-in flex-row items-start gap-3">
      <div className="mt-1 shrink-0">
        <AgentAvatar size="sm" isTyping={isStreaming} persona={activePersona} />
      </div>

      <div className="group relative max-w-[88%] overflow-hidden rounded-2xl rounded-tl-sm border border-dark-500 bg-dark-800/95 shadow-[0_8px_30px_rgba(0,0,0,0.25)] sm:max-w-[80%]">
        <div className="flex items-center gap-2 border-b border-dark-600/80 bg-dark-700/60 px-4 py-2">
          <span className="text-xs font-semibold text-gray-200">
            {PERSONA_NAMES[activePersona]}
          </span>
          <span
            className={`rounded-full border px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-[0.12em] ${PERSONA_BADGES[activePersona]}`}
          >
            Agent
          </span>
          {isStreaming && (
            <span className="ml-auto flex items-center gap-1.5 text-[10px] text-accent-cyan">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent-cyan" />
              Streaming
            </span>
          )}
        </div>

        <div className="px-4 py-3">
          <div className="md-content text-sm leading-relaxed text-gray-200">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
              {content}
            </ReactMarkdown>
            {isStreaming && (
              <span className="ml-0.5 inline-block h-4 w-2 animate-blink bg-accent-cyan align-middle" />
            )}
          </div>
        </div>

        {!isStreaming && content && (
          <button
            onClick={handleCopy}
            className="absolute right-2 top-2 rounded-md bg-dark-600/80 p-1.5 opacity-0 transition-opacity hover:bg-dark-500 group-hover:opacity-100"
            title="Copy"
          >
            {copied ? (
              <Check size={12} className="text-green-400" />
            ) : (
              <Copy size={12} />
            )}
          </button>
        )}

        {!isStreaming && message.created_at && (
          <div className="px-4 pb-2 text-right text-[10px] text-gray-600">
            {new Date(message.created_at).toLocaleTimeString()}
          </div>
        )}
      </div>
    </div>
  );
}
