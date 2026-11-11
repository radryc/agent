import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import AppLayout from "./components/layout/AppLayout";
import AuthGate from "./components/layout/AuthGate";
import ChatPage from "./pages/ChatPage";
import DashboardPage from "./pages/DashboardPage";
import SettingsPage from "./pages/SettingsPage";
import LandingPage from "./pages/LandingPage";
import EmbedPage from "./pages/EmbedPage";
import NightwatchPage from "./pages/NightwatchPage";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* The embed drawer is used inside Doctor, Guardian, and MonoFS. It
            must render without an interactive SSO gate; the backend still
            applies the agent's machine identity to its tools. */}
        <Route path="/embed/ask" element={<EmbedPage />} />
        <Route
          element={
            <AuthGate>
              <AppLayout />
            </AuthGate>
          }
        >
          <Route path="/" element={<LandingPage />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/chat/:sessionId" element={<ChatPage />} />
          <Route path="/nightwatch" element={<NightwatchPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
