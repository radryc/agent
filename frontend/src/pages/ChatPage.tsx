import { useEffect, useRef, useCallback } from "react";
import { useParams } from "react-router-dom";
import { useChatStore } from "../stores/chatStore";
import Terminal, { type TerminalHandle } from "../components/terminal/Terminal";

const AGENT_PROMPTS: Record<string, string> = {
  local: "$ ",
  opencode: "\x1b[35mopencode\x1b[0m $ ",
};

export default function ChatPage() {
  const { sessionId } = useParams();
  const terminalRef = useRef<TerminalHandle>(null);
  const {
    activeSessionId,
    activeAgent,
    switchSession,
    sendMessage,
    isStreaming,
    streamContent,
    messages,
  } = useChatStore();

  const prompt = AGENT_PROMPTS[activeAgent] || "$ ";

  useEffect(() => {
    if (sessionId && sessionId !== activeSessionId) {
      switchSession(sessionId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // Replay history into terminal when switching sessions
  useEffect(() => {
    const term = terminalRef.current;
    if (!term) return;
    term.clear();
    term.printWelcome();
    for (const msg of messages) {
      if (msg.role === "user") {
        term.writeln(msg.content);
      } else {
        term.writeln("");
        term.write(msg.content);
        term.write("\r\n");
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSessionId]);

  // Write streaming tokens to terminal
  const prevLenRef = useRef(0);
  useEffect(() => {
    const term = terminalRef.current;
    if (!term || !isStreaming) {
      prevLenRef.current = 0;
      return;
    }
    const delta = streamContent.slice(prevLenRef.current);
    if (delta) {
      term.write(delta);
      prevLenRef.current = streamContent.length;
    }
  }, [streamContent, isStreaming]);

  // When streaming starts, add spacing
  useEffect(() => {
    if (isStreaming) {
      terminalRef.current?.writeln("");
    }
  }, [isStreaming]);

  const handleSend = useCallback(
    (text: string) => {
      sendMessage(text);
    },
    [sendMessage]
  );

  const handleCancel = useCallback(() => {
    // Cancel functionality can be added later
  }, []);

  return (
    <div className="flex flex-col h-full bg-dark-900">
      {/* Status bar */}
      <div className="shrink-0 flex items-center gap-3 px-4 py-2 border-b border-dark-600 bg-dark-800/80 text-xs">
        <span className="text-gray-500">Session</span>
        <span className="text-gray-300 font-mono text-[11px]">
          {activeSessionId?.slice(0, 8) ?? "..."}
        </span>
        <span className="text-gray-700">|</span>
        <span className="text-gray-500">Agent</span>
        <span className="text-accent-blue font-medium">
          {useChatStore.getState().activeAgent === "local" ? "Local" : "OpenCode"}
        </span>
        {isStreaming && (
          <>
            <span className="text-gray-700">|</span>
            <span className="flex items-center gap-1.5 text-accent-cyan">
              <span className="w-1.5 h-1.5 rounded-full bg-accent-cyan animate-pulse" />
              Streaming
            </span>
          </>
        )}
      </div>

      {/* Terminal */}
      <div className="flex-1 min-h-0">
        <Terminal
          ref={terminalRef}
          onSend={handleSend}
          onCancel={handleCancel}
          streaming={isStreaming}
          prompt={prompt}
        />
      </div>
    </div>
  );
}
