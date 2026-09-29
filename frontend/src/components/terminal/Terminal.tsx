import {
  forwardRef,
  useEffect,
  useRef,
  useImperativeHandle,
  useCallback,
} from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebglAddon } from "@xterm/addon-webgl";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";

import type { Mode } from "../../types";

export interface TerminalHandle {
  write: (text: string) => void;
  writeln: (text: string) => void;
  clear: () => void;
  focus: () => void;
  setPrompt: (prompt: string) => void;
  printWelcome: () => void;
}

interface TerminalProps {
  onSend: (text: string) => void;
  onTerminalStart: () => void;
  onTerminalInput: (data: string) => void;
  onTerminalResize: (cols: number, rows: number) => void;
  onTerminalSignal: (signal?: string) => void;
  onTerminalHistory: (command: string) => void;
  onCancel?: () => void;
  streaming: boolean;
  prompt?: string;
  mode: Mode;
  cwd: string;
  onSetMode: (mode: Mode) => void;
}

const Terminal = forwardRef<TerminalHandle, TerminalProps>(
  (
    {
      onSend,
      onTerminalStart,
      onTerminalInput,
      onTerminalResize,
      onTerminalSignal,
      onTerminalHistory,
      onCancel,
      streaming,
      prompt = "$ ",
      mode,
      cwd,
      onSetMode,
    },
    ref
  ) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const xtermRef = useRef<XTerm | null>(null);
    const fitAddonRef = useRef<FitAddon>(new FitAddon());
    const bufferRef = useRef("");
    const historyRef = useRef<string[]>([]);
    const historyIdxRef = useRef(-1);
    const savedLineRef = useRef("");
    const promptRef = useRef(prompt);
    const terminalLineRef = useRef("");
    const streamingLock = useRef(false);
    const modeRef = useRef(mode);

    promptRef.current = mode === "terminal" ? `${cwd}$ ` : prompt;
    modeRef.current = mode;

    const writePrompt = useCallback((leadingNewline = true) => {
      const t = xtermRef.current;
      if (!t) return;
      if (modeRef.current === "terminal") return;
      t.write(leadingNewline ? "\r\n" : "\r");
      t.write(promptRef.current);
    }, []);

    const cancelLine = useCallback((showPrompt = true) => {
      const t = xtermRef.current;
      if (!t) return;
      if (bufferRef.current) {
        t.write("^C");
      }
      bufferRef.current = "";
      if (showPrompt) {
        writePrompt();
      }
    }, [writePrompt]);

    const handleEnter = useCallback(() => {
      const line = bufferRef.current.trim();
      bufferRef.current = "";
      xtermRef.current?.write("\r\n");

      if (line.startsWith("/mode")) {
        const parts = line.split(/\s+/);
        let nextMode: Mode = modeRef.current;
        if (parts.length === 1 || parts[1] === "terminal") {
          if (modeRef.current !== "terminal") {
            nextMode = "terminal";
            onSetMode("terminal");
            xtermRef.current?.writeln("\x1b[33mSwitched to terminal mode\x1b[0m");
          }
        } else if (parts[1] === "agent") {
          if (modeRef.current !== "agent") {
            nextMode = "agent";
            onSetMode("agent");
            xtermRef.current?.writeln("\x1b[33mSwitched to agent mode\x1b[0m");
          }
        }
        if (nextMode === "agent") {
          writePrompt(false);
        }
        return;
      }

      if (line) {
        historyRef.current.push(line);
      }
      historyIdxRef.current = -1;
      savedLineRef.current = "";
      if (line) {
        onSend(line);
      } else {
        writePrompt(false);
      }
    }, [onSend, writePrompt, onSetMode]);

    const trackTerminalHistory = useCallback(
      (data: string) => {
        if (data === "\r") {
          const cmd = terminalLineRef.current.trim();
          if (cmd) {
            onTerminalHistory(cmd);
          }
          terminalLineRef.current = "";
          return;
        }

        if (data === "\u007f") {
          terminalLineRef.current = terminalLineRef.current.slice(0, -1);
          return;
        }

        if (data.length === 1 && data >= " " && data !== "\u007f") {
          terminalLineRef.current += data;
        }
      },
      [onTerminalHistory]
    );

    const handleBackspace = useCallback(() => {
      const t = xtermRef.current;
      if (!t || !bufferRef.current) return;
      bufferRef.current = bufferRef.current.slice(0, -1);
      t.write("\b \b");
    }, []);

    const handleHistoryUp = useCallback(() => {
      const t = xtermRef.current;
      if (!t) return;
      const hist = historyRef.current;
      if (hist.length === 0) return;

      if (historyIdxRef.current === -1) {
        savedLineRef.current = bufferRef.current;
      }

      if (historyIdxRef.current < hist.length - 1) {
        historyIdxRef.current++;
      }

      const entry = hist[hist.length - 1 - historyIdxRef.current];
      const current = bufferRef.current;

      // Clear current line
      if (current) {
        t.write("\r" + promptRef.current + " ".repeat(current.length) + "\r" + promptRef.current);
      } else {
        t.write("\r" + promptRef.current);
      }

      bufferRef.current = entry;
      t.write(entry);
    }, []);

    const handleHistoryDown = useCallback(() => {
      const t = xtermRef.current;
      if (!t) return;

      if (historyIdxRef.current > 0) {
        historyIdxRef.current--;
        const entry = historyRef.current[historyRef.current.length - 1 - historyIdxRef.current];
        const current = bufferRef.current;
        t.write("\r" + promptRef.current + " ".repeat(current.length) + "\r" + promptRef.current);
        bufferRef.current = entry;
        t.write(entry);
      } else if (historyIdxRef.current === 0) {
        historyIdxRef.current = -1;
        const current = bufferRef.current;
        t.write("\r" + promptRef.current + " ".repeat(current.length) + "\r" + promptRef.current);
        bufferRef.current = savedLineRef.current;
        t.write(savedLineRef.current);
        savedLineRef.current = "";
      }
    }, []);

    useImperativeHandle(ref, () => ({
      write: (text: string) => {
        xtermRef.current?.write(text.replace(/\n/g, "\r\n"));
      },
      writeln: (text: string) => {
        xtermRef.current?.write(text.replace(/\n/g, "\r\n") + "\r\n");
      },
      clear: () => {
        xtermRef.current?.clear();
      },
      focus: () => {
        xtermRef.current?.focus();
      },
      setPrompt: (p: string) => {
        promptRef.current = p;
      },
      printWelcome: () => {
        const t = xtermRef.current;
        if (!t) return;
        t.write("\r\x1b[2K");

        const title = "Outage Otto — SRE assistant";
        const hint = "Type a request, /help for commands, Ctrl+C to cancel";
        const width = Math.max(title.length, hint.length) + 2;
        const pad = (len: number) => " ".repeat(width - len - 1);

        t.writeln(
          `\x1b[1;36m╭${"─".repeat(width)}╮\x1b[0m`
        );
        t.writeln(
          `\x1b[1;36m│\x1b[0m \x1b[1mOutage Otto\x1b[0m — SRE assistant${pad(title.length)}\x1b[1;36m│\x1b[0m`
        );
        t.writeln(
          `\x1b[1;36m│\x1b[0m \x1b[2m${hint}\x1b[0m${pad(hint.length)}\x1b[1;36m│\x1b[0m`
        );
        t.writeln(
          `\x1b[1;36m╰${"─".repeat(width)}╯\x1b[0m`
        );
        writePrompt(false);
      },
    }));

    useEffect(() => {
      if (!containerRef.current) return;

      const term = new XTerm({
        theme: {
          background: "#0a0a0f",
          foreground: "#e2e8f0",
          cursor: "#3b82f6",
          cursorAccent: "#0a0a0f",
          selectionBackground: "#3b82f644",
          black: "#1a1a25",
          red: "#ef4444",
          green: "#22c55e",
          yellow: "#f59e0b",
          blue: "#3b82f6",
          magenta: "#9333ea",
          cyan: "#06b6d4",
          white: "#e2e8f0",
          brightBlack: "#32324a",
          brightRed: "#f87171",
          brightGreen: "#4ade80",
          brightYellow: "#fbbf24",
          brightBlue: "#60a5fa",
          brightMagenta: "#a855f7",
          brightCyan: "#22d3ee",
          brightWhite: "#f8fafc",
        },
        fontSize: 14,
        fontFamily:
          "'JetBrains Mono', 'Fira Code', 'Cascadia Code', 'Menlo', 'Consolas', monospace",
        cursorBlink: true,
        cursorStyle: "bar",
        allowProposedApi: true,
        scrollback: 10000,
        lineHeight: 1.5,
        letterSpacing: 0,
        disableStdin: false,
      });

      const fitAddon = fitAddonRef.current;
      term.loadAddon(fitAddon);

      try {
        term.loadAddon(new WebglAddon());
      } catch {
        // fallback to canvas
      }

      term.loadAddon(new WebLinksAddon());

      term.open(containerRef.current);
      let fitFrame = 0;
      const fitAndNotify = () => {
        if (fitFrame) {
          cancelAnimationFrame(fitFrame);
        }
        fitFrame = requestAnimationFrame(() => {
          fitAddon.fit();
          onTerminalResize(term.cols, term.rows);
        });
      };
      fitAndNotify();
      const delayedFit = window.setTimeout(fitAndNotify, 80);
      if ("fonts" in document) {
        document.fonts.ready.then(() => fitAndNotify());
      }

      // Resize handling
      const observer = new ResizeObserver(() => {
        fitAndNotify();
      });
      observer.observe(containerRef.current);
      const handleWindowResize = () => fitAndNotify();
      const handleVisibilityChange = () => {
        if (document.visibilityState === "visible") {
          fitAndNotify();
        }
      };
      window.addEventListener("resize", handleWindowResize);
      document.addEventListener("visibilitychange", handleVisibilityChange);

      // Input handling
      term.onData((data) => {
        const code = data.charCodeAt(0);

        // Ctrl+E — toggle mode (always available, even while streaming)
        if (code === 5) {
          const next: Mode = modeRef.current === "agent" ? "terminal" : "agent";
          onSetMode(next);
          cancelLine(next !== "terminal");
          const label = next === "terminal" ? "terminal" : "agent";
          term.writeln(`\x1b[33mSwitched to ${label} mode\x1b[0m`);
          if (next === "agent") {
            writePrompt(false);
          }
          return;
        }

        if (modeRef.current === "terminal") {
          if (code === 3) {
            onTerminalSignal("SIGINT");
            return;
          }
          trackTerminalHistory(data);
          onTerminalInput(data);
          return;
        }

        if (streamingLock.current) return;

        if (code === 13) {
          handleEnter();
        } else if (code === 127) {
          handleBackspace();
        } else if (code === 3) {
          // Ctrl+C
          if (bufferRef.current) {
            cancelLine();
          }
          onCancel?.();
        } else if (code === 12) {
          // Ctrl+L
          term.clear();
          writePrompt(false);
        } else if (code === 4 && !bufferRef.current) {
          // Ctrl+D on empty line - do nothing for now
        } else if (code === 27 && data.length > 1 && data[1] === "[") {
          if (data === "\x1b[A") {
            handleHistoryUp();
          } else if (data === "\x1b[B") {
            handleHistoryDown();
          }
          // Ignore other escape sequences
        } else if (code >= 32 || code === 9) {
          // Printable chars + tab
          bufferRef.current += data;
          term.write(data);
        }
      });

      xtermRef.current = term;
      fitAndNotify();

      return () => {
        window.clearTimeout(delayedFit);
        if (fitFrame) {
          cancelAnimationFrame(fitFrame);
        }
        window.removeEventListener("resize", handleWindowResize);
        document.removeEventListener("visibilitychange", handleVisibilityChange);
        observer.disconnect();
        term.dispose();
        xtermRef.current = null;
      };
    }, []);

    // Sync streaming lock with prop
    useEffect(() => {
      streamingLock.current = streaming;
    }, [streaming]);

    useEffect(() => {
      if (mode === "terminal") {
        onTerminalStart();
      }
    }, [mode, onTerminalStart]);

    useEffect(() => {
      const fitAddon = fitAddonRef.current;
      const term = xtermRef.current;
      if (!term) return;
      fitAddon.fit();
      onTerminalResize(term.cols, term.rows);
    }, [mode, onTerminalResize]);

    // Show new prompt after streaming finishes
    const prevStreamingRef = useRef(streaming);
    useEffect(() => {
      if (mode === "agent" && prevStreamingRef.current && !streaming) {
        writePrompt();
      }
      prevStreamingRef.current = streaming;
    }, [streaming, writePrompt, mode]);

    return (
      <div
        ref={containerRef}
        className="w-full h-full"
        style={{ backgroundColor: "#0a0a0f" }}
      />
    );
  }
);

Terminal.displayName = "Terminal";

export default Terminal;
