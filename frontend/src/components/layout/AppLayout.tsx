import { useState } from "react";
import { Outlet } from "react-router-dom";
import Sidebar from "./Sidebar";
import { Menu } from "lucide-react";

export default function AppLayout() {
  const [sidebarOpen, setSidebarOpen] = useState(true);

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-dark-900">
      {/* Sidebar: off-canvas drawer on mobile, collapsible column on desktop */}
      <div
        className={`fixed inset-y-0 left-0 z-40 w-72 transition-all duration-200 ease-in-out lg:static lg:z-auto lg:shrink-0 lg:translate-x-0 lg:overflow-hidden ${
          sidebarOpen ? "translate-x-0 lg:w-72" : "-translate-x-full lg:w-0"
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
      <main className="relative flex-1 flex flex-col min-w-0">
        {/* Only shown while the sidebar is hidden, so it never covers the
            sidebar header/avatar. */}
        {!sidebarOpen && (
          <button
            onClick={() => setSidebarOpen(true)}
            title="Show sidebar"
            aria-label="Show sidebar"
            className="fixed top-3 left-3 z-50 rounded-lg bg-dark-700 p-2 hover:bg-dark-600 transition-colors"
          >
            <Menu size={20} />
          </button>
        )}
        <Outlet />
      </main>
    </div>
  );
}
