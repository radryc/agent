import { useLocation, Link } from "react-router-dom";
import { Menu, Settings } from "lucide-react";
import SsoStatusButton from "./SsoStatusButton";
import CommandPalette from "./CommandPalette";
import ThemePicker from "./ThemePicker";

const TITLES: Record<string, string> = {
  "/": "Home",
  "/chat": "Chat",
  "/nightwatch": "Nightwatch",
  "/settings": "Settings",
};

export default function TopBar({ onToggleSidebar }: { onToggleSidebar: () => void }) {
  const { pathname } = useLocation();
  const title =
    pathname.startsWith("/chat/")
      ? "Chat"
      : TITLES[pathname] ?? "Agent";

  return (
    <header className="s-topbar shrink-0">
      <button
        type="button"
        className="s-icon-btn lg:hidden"
        onClick={onToggleSidebar}
        aria-label="Toggle sidebar"
      >
        <Menu size={18} />
      </button>
      <div className="s-topbar__crumb">
        <span>Agent</span>
        <span className="s-topbar__sep">/</span>
        <strong className="s-topbar__title">{title}</strong>
      </div>
      <div className="s-topbar__spacer" />
      <div className="s-topbar__actions">
        <CommandPalette />
        <ThemePicker />
        <SsoStatusButton />
        <Link
          to="/settings"
          className="s-icon-btn"
          aria-label="Settings"
          title="Settings"
        >
          <Settings size={16} />
        </Link>
      </div>
    </header>
  );
}
