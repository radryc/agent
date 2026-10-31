import { useEffect, useState } from "react";
import { Outlet } from "react-router-dom";
import Sidebar from "./Sidebar";
import TopBar from "./TopBar";

export default function AppLayout() {
  const [sidebarOpen, setSidebarOpen] = useState(() =>
    typeof window === "undefined" ? true : window.matchMedia("(min-width: 1024px)").matches
  );

  useEffect(() => {
    const media = window.matchMedia("(min-width: 1024px)");
    const onChange = () => setSidebarOpen(media.matches);
    media.addEventListener?.("change", onChange);
    return () => media.removeEventListener?.("change", onChange);
  }, []);

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-dark-900">
      <a className="s-skip-link" href="#main-content">
        Skip to content
      </a>

      {/* Sidebar: off-canvas drawer on mobile, collapsible column on desktop */}
      <div
        className={`fixed inset-y-0 left-0 z-40 w-[236px] transition-all duration-200 ease-in-out lg:static lg:z-auto lg:shrink-0 lg:translate-x-0 lg:overflow-hidden ${
          sidebarOpen ? "translate-x-0 lg:w-[236px]" : "-translate-x-full lg:w-0"
        }`}
      >
        <Sidebar onClose={() => setSidebarOpen(false)} />
      </div>

      {/* Backdrop for mobile */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/50 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Main content */}
      <main
        id="main-content"
        className="relative flex min-w-0 flex-1 flex-col"
      >
        <TopBar onToggleSidebar={() => setSidebarOpen((open) => !open)} />
        <div className="flex min-h-0 flex-1 flex-col">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
