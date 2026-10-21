import { useEffect, useRef, useCallback, useState } from "react";
import { useParams } from "react-router-dom";
import { useChatStore } from "../stores/chatStore";
import type { Mode } from "../types";
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
    activePersona,
    streamPersona,
    streamEngine,
    sessions,
    switchSession,
    sendMessage,
    renameSession,
    isStreaming,
    streamContent,
    messages,
    mode,
    cwd,
    execOutput,
    setMode,
    startTerminal,
    sendTerminalInput,
    sendTerminalResize,
    sendTerminalSignal,
    addTerminalHistory,
    terminalConnected,
  } = useChatStore();

  const activeSession = sessions.find((s) => s.id === activeSessionId);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");

  const prompt = AGENT_PROMPTS[activeAgent] || "$ ";

  const personaLabel =
    activePersona === "auto"
      ? streamPersona
        ? streamPersona === "steve"
          ? "Steve"
          : "Otto"
        : "Auto"
      : activePersona === "steve"
        ? "Steve"
        : "Otto";

  useEffect(() => {
    if (sessionId && sessionId !== activeSessionId) {
      switchSession(sessionId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // Replay history into terminal when switching sessions
  useEffect(() => {
    if (mode === "terminal") return;
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
  }, [activeSessionId, mode]);

  // Write streaming tokens to terminal
  const prevLenRef = useRef(0);
  useEffect(() => {
    const term = terminalRef.current;
    if (!term) {
      prevLenRef.current = 0;
      return;
    }

    if (mode !== "terminal" && !isStreaming) {
      prevLenRef.current = 0;
      return;
    }

    const src = mode === "terminal" ? execOutput : streamContent;
    const delta = src.slice(prevLenRef.current);
    if (delta) {
      term.write(delta);
      prevLenRef.current = src.length;
    }
  }, [streamContent, execOutput, isStreaming, mode]);

  // When streaming starts in agent mode, add spacing
  useEffect(() => {
    if (isStreaming && mode !== "terminal") {
      terminalRef.current?.writeln("");
    }
  }, [isStreaming, mode]);

  const handleSend = useCallback(
    (text: string) => {
      sendMessage(text);
    },
    [sendMessage]
  );

  const handleCancel = useCallback(() => {
    sendTerminalSignal("SIGINT");
  }, [sendTerminalSignal]);

  const handleSetMode = useCallback(
    (newMode: Mode) => {
      setMode(newMode);
    },
    [setMode]
  );

  const handleTerminalStart = useCallback(() => {
    startTerminal();
  }, [startTerminal]);

  const handleTerminalInput = useCallback(
    (data: string) => {
      sendTerminalInput(data);
    },
    [sendTerminalInput]
  );

  const handleTerminalResize = useCallback(
    (cols: number, rows: number) => {
      sendTerminalResize(cols, rows);
    },
    [sendTerminalResize]
  );

  const handleTerminalSignal = useCallback(
    (name?: string) => {
      sendTerminalSignal(name || "SIGINT");
    },
    [sendTerminalSignal]
  );

  const handleTerminalHistory = useCallback(
    (command: string) => {
      addTerminalHistory(command);
    },
    [addTerminalHistory]
  );

  return (
    <div className="flex flex-col h-full bg-dark-900">
      {/* Status bar */}
      <div className="shrink-0 flex items-center gap-3 px-4 py-2 border-b border-dark-600 bg-dark-800/80 text-xs">
        <span className="text-gray-500">Session</span>
        {editingTitle && activeSessionId ? (
          <input
            className="bg-dark-700 border border-accent-blue rounded px-1.5 py-0.5 text-xs text-gray-100 outline-none font-mono w-40"
            value={titleDraft}
            onChange={(e) => setTitleDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                renameSession(activeSessionId, titleDraft);
                setEditingTitle(false);
              } else if (e.key === "Escape") {
                setEditingTitle(false);
              }
            }}
            onBlur={() => {
              if (titleDraft.trim()) {
                renameSession(activeSessionId, titleDraft);
              }
              setEditingTitle(false);
            }}
            autoFocus
          />
        ) : (
          <span
            className="text-gray-300 font-mono text-[11px] cursor-pointer hover:text-accent-blue transition-colors"
            title="Double-click to rename"
            onDoubleClick={() => {
              if (activeSessionId) {
                setEditingTitle(true);
                setTitleDraft(activeSession?.title || "New Chat");
              }
            }}
          >
            {activeSession?.title || activeSessionId?.slice(0, 8) || "..."}
          </span>
        )}
        <span className="text-gray-700">|</span>
        <span className="text-gray-500">Persona</span>
        <span className="text-accent-purple font-medium">{personaLabel}</span>
        {streamEngine && activePersona === "auto" && streamPersona && (
          <span className="text-gray-600 text-[10px]">{streamEngine}</span>
        )}
        <span className="text-gray-700">|</span>
        <span className="text-gray-500">Mode</span>
        <span
          className={`font-medium cursor-pointer ${mode === "terminal" ? "text-accent-green" : "text-accent-purple"}`}
          onClick={() => setMode(mode === "terminal" ? "agent" : "terminal")}
          title="Ctrl+E to toggle"
        >
          {mode === "terminal" ? "Terminal" : "Agent"}
        </span>
        <span className="text-gray-600 text-[10px]">Ctrl+E</span>
        {mode === "terminal" && (
          <>
            <span className="text-gray-700">|</span>
            <span
              className={`font-medium ${terminalConnected ? "text-accent-green" : "text-amber-400"}`}
            >
              {terminalConnected ? "Shell Connected" : "Shell Connecting"}
            </span>
          </>
        )}
        {isStreaming && mode !== "terminal" && (
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
          onTerminalStart={handleTerminalStart}
          onTerminalInput={handleTerminalInput}
          onTerminalResize={handleTerminalResize}
          onTerminalSignal={handleTerminalSignal}
          onTerminalHistory={handleTerminalHistory}
          onCancel={handleCancel}
          streaming={isStreaming}
          prompt={prompt}
          mode={mode}
          cwd={cwd}
          onSetMode={handleSetMode}
        />
      </div>
    </div>
  );
}
