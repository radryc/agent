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
  onCancel?: () => void;
  streaming: boolean;
  prompt?: string;
}

const Terminal = forwardRef<TerminalHandle, TerminalProps>(
  ({ onSend, onCancel, streaming, prompt = "$ " }, ref) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const xtermRef = useRef<XTerm | null>(null);
    const fitAddonRef = useRef<FitAddon>(new FitAddon());
    const bufferRef = useRef("");
    const historyRef = useRef<string[]>([]);
    const historyIdxRef = useRef(-1);
    const savedLineRef = useRef("");
    const promptRef = useRef(prompt);
    const streamingLock = useRef(false);

    promptRef.current = prompt;

    const writePrompt = useCallback(() => {
      const t = xtermRef.current;
      if (!t) return;
      t.write("\r\n");
      t.write(promptRef.current);
    }, []);

    const cancelLine = useCallback(() => {
      const t = xtermRef.current;
      if (!t) return;
      if (bufferRef.current) {
        t.write("^C");
      }
      bufferRef.current = "";
      writePrompt();
    }, [writePrompt]);

    const handleEnter = useCallback(() => {
      const line = bufferRef.current.trim();
      bufferRef.current = "";
      xtermRef.current?.write("\r\n");
      if (line) {
        historyRef.current.push(line);
      }
      historyIdxRef.current = -1;
      savedLineRef.current = "";
      if (line) {
        onSend(line);
      } else {
        writePrompt();
      }
    }, [onSend, writePrompt]);

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
        xtermRef.current?.write(text);
      },
      writeln: (text: string) => {
        xtermRef.current?.writeln(text);
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
        t.writeln(
          "\x1b[1;36m╭──────────────────────────────────────────────╮\x1b[0m"
        );
        t.writeln(
          "\x1b[1;36m│\x1b[0m   \x1b[1mAI Coding Agent\x1b[0m — programming assistant     \x1b[1;36m│\x1b[0m"
        );
        t.writeln(
          "\x1b[1;36m│\x1b[0m   \x1b[2mType a request, /help for commands, Ctrl+C to cancel\x1b[0m  \x1b[1;36m│\x1b[0m"
        );
        t.writeln(
          "\x1b[1;36m╰──────────────────────────────────────────────╯\x1b[0m"
        );
        writePrompt();
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
      fitAddon.fit();

      // Resize handling
      const observer = new ResizeObserver(() => {
        fitAddon.fit();
      });
      observer.observe(containerRef.current);

      // Input handling
      term.onData((data) => {
        if (streamingLock.current) return;

        const code = data.charCodeAt(0);

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
          writePrompt();
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
      writePrompt();

      return () => {
        observer.disconnect();
        term.dispose();
        xtermRef.current = null;
      };
    }, []);

    // Sync streaming lock with prop
    useEffect(() => {
      streamingLock.current = streaming;
    }, [streaming]);

    // Show new prompt after streaming finishes
    useEffect(() => {
      if (!streaming) {
        writePrompt();
      }
    }, [streaming, writePrompt]);

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
