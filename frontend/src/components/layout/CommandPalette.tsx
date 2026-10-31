import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { MessageSquare, Search, Settings, Terminal } from "lucide-react";
import { useChatStore } from "../../stores/chatStore";

interface Command {
  group: string;
  label: string;
  hint?: string;
  run: () => void;
}

export default function CommandPalette() {
  const navigate = useNavigate();
  const { sessions } = useChatStore();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  const commands = useMemo<Command[]>(() => {
    const go = (to: string) => () => {
      setOpen(false);
      navigate(to);
    };
    const items: Command[] = [
      { group: "Go to", label: "Agent home", hint: "/", run: go("/") },
      { group: "Go to", label: "New chat", hint: "/chat", run: go("/chat") },
      { group: "Go to", label: "Nightwatch", hint: "/nightwatch", run: go("/nightwatch") },
      { group: "Go to", label: "Settings", hint: "/settings", run: go("/settings") },
    ];
    for (const session of sessions) {
      items.push({
        group: "Sessions",
        label: session.title,
        hint: session.id.slice(0, 8),
        run: go(`/chat/${session.id}`),
      });
    }
    return items;
  }, [navigate, sessions]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return commands.filter((command) =>
      `${command.group} ${command.label}`.toLowerCase().includes(needle)
    );
  }, [commands, query]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing =
        target?.tagName === "INPUT" ||
        target?.tagName === "TEXTAREA" ||
        target?.isContentEditable;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((value) => !value);
      } else if (!typing && event.key === "/") {
        event.preventDefault();
        setOpen(true);
      } else if (event.key === "Escape") {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActive(0);
    const timer = window.setTimeout(() => inputRef.current?.focus(), 20);
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          'input, button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'
        )
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  useEffect(() => setActive(0), [query]);

  const run = (command: Command | undefined) => {
    if (!command) return;
    setOpen(false);
    command.run();
  };

  return (
    <>
      <button
        type="button"
        className="s-icon-btn"
        title="Quick jump (Ctrl/Cmd-K)"
        aria-label="Quick jump"
        onClick={() => setOpen(true)}
      >
        <Search size={16} />
      </button>
      {open && (
        <div
          className="fixed inset-0 z-[300] grid place-items-start justify-center bg-[var(--strata-scrim)] px-4 pt-[12vh] backdrop-blur-sm"
          role="dialog"
          aria-modal="true"
          aria-label="Quick jump"
          onClick={(event) => {
            if (event.target === event.currentTarget) setOpen(false);
          }}
        >
          <div ref={dialogRef} className="w-full max-w-[620px] overflow-hidden rounded-xl border border-dark-600 bg-dark-800 shadow-2xl">
            <input
              ref={inputRef}
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "ArrowDown") {
                  event.preventDefault();
                  setActive((value) => Math.min(value + 1, filtered.length - 1));
                } else if (event.key === "ArrowUp") {
                  event.preventDefault();
                  setActive((value) => Math.max(value - 1, 0));
                } else if (event.key === "Enter") {
                  event.preventDefault();
                  run(filtered[active]);
                } else if (event.key === "Escape") {
                  setOpen(false);
                }
              }}
              placeholder="Jump to a page or session…"
              autoComplete="off"
              className="min-h-12 w-full border-0 border-b border-dark-600 bg-dark-900 px-4 py-3 text-sm text-gray-100 outline-none"
            />
            <div className="max-h-[58vh] overflow-y-auto p-1.5">
              {filtered.length === 0 && (
                <div className="p-5 text-center text-xs text-gray-500">
                  No matching commands.
                </div>
              )}
              {filtered.map((command, index) => (
                <div key={`${command.group}-${command.label}`}>
                  {filtered[index - 1]?.group !== command.group && (
                    <div className="px-2.5 pb-1 pt-2 text-[10px] font-bold uppercase tracking-[0.08em] text-gray-500">
                      {command.group}
                    </div>
                  )}
                  <button
                    type="button"
                    className={`flex w-full items-center justify-between gap-3 rounded-md px-2.5 py-2 text-left text-sm ${
                      index === active
                        ? "bg-accent-blue/15 text-gray-100"
                        : "text-gray-400 hover:bg-dark-700 hover:text-gray-200"
                    }`}
                    onMouseEnter={() => setActive(index)}
                    onClick={() => run(command)}
                  >
                    <span className="flex items-center gap-2 truncate">
                      {command.group === "Sessions" ? (
                        <MessageSquare size={14} />
                      ) : command.label === "Settings" ? (
                        <Settings size={14} />
                      ) : (
                        <Terminal size={14} />
                      )}
                      <span className="truncate">{command.label}</span>
                    </span>
                    {command.hint && (
                      <span className="shrink-0 font-mono text-[10px] text-gray-500">
                        {command.hint}
                      </span>
                    )}
                  </button>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
