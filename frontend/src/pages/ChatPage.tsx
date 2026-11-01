import { useEffect, useRef, useCallback, useState } from "react";
import { useParams } from "react-router-dom";
import { useChatStore } from "../stores/chatStore";
import type { Mode } from "../types";
import Terminal, { type TerminalHandle } from "../components/terminal/Terminal";
import ChatMessages from "../components/chat/ChatMessages";
import ChatInput from "../components/chat/ChatInput";
import SsoStatusButton from "../components/layout/SsoStatusButton";
import ContextRail from "../components/chat/ContextRail";
import NightwatchBanner from "../components/nightwatch/NightwatchBanner";

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

  // In agent mode the conversation is rendered as chat bubbles. The terminal
  // is only mounted in terminal mode, where it streams shell output directly.
  const prevLenRef = useRef(0);
  useEffect(() => {
    const term = terminalRef.current;
    if (!term || mode !== "terminal") {
      prevLenRef.current = 0;
      return;
    }
    const delta = execOutput.slice(prevLenRef.current);
    if (delta) {
      term.write(delta);
      prevLenRef.current = execOutput.length;
    }
  }, [execOutput, mode]);

  // Ctrl+E toggles mode. In terminal mode xterm handles the shortcut itself.
  useEffect(() => {
    if (mode !== "agent") return;
    const onKey = (event: KeyboardEvent) => {
      if (event.ctrlKey && event.key.toLowerCase() === "e") {
        event.preventDefault();
        setMode("terminal");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mode, setMode]);

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
          <button
            type="button"
            className="font-mono text-[11px] text-gray-300 transition-colors hover:text-accent-blue"
            title="Double-click to rename"
            onDoubleClick={() => {
              if (activeSessionId) {
                setEditingTitle(true);
                setTitleDraft(activeSession?.title || "New Chat");
              }
            }}
          >
            {activeSession?.title || activeSessionId?.slice(0, 8) || "..."}
          </button>
        )}
        <span className="text-gray-700">|</span>
        <span className="text-gray-500">Persona</span>
        <span className="text-accent-purple font-medium">{personaLabel}</span>
        {streamEngine && activePersona === "auto" && streamPersona && (
          <span className="text-gray-600 text-[10px]">{streamEngine}</span>
        )}
        <span className="text-gray-700">|</span>
        <span className="text-gray-500">Mode</span>
        <button
          type="button"
          className={`font-medium ${mode === "terminal" ? "text-accent-green" : "text-accent-purple"}`}
          onClick={() => setMode(mode === "terminal" ? "agent" : "terminal")}
          title="Ctrl+E to toggle"
        >
          {mode === "terminal" ? "Terminal" : "Agent"}
        </button>
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
        <div className="ml-auto pl-2">
          <SsoStatusButton />
        </div>
      </div>

      <NightwatchBanner />

      {mode === "agent" ? (
        <div className="flex min-h-0 flex-1">
          <div className="flex min-w-0 flex-1 flex-col">
            <ChatMessages />
            <ChatInput />
          </div>
          <ContextRail />
        </div>
      ) : (
        <div className="min-h-0 flex-1">
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
      )}
    </div>
  );
}
