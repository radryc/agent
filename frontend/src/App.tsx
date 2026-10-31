import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import AppLayout from "./components/layout/AppLayout";
import AuthGate from "./components/layout/AuthGate";
import ChatPage from "./pages/ChatPage";
import SettingsPage from "./pages/SettingsPage";
import LandingPage from "./pages/LandingPage";
import EmbedPage from "./pages/EmbedPage";
import NightwatchPage from "./pages/NightwatchPage";

export default function App() {
  return (
    <BrowserRouter>
      <AuthGate>
        <Routes>
          <Route path="/embed/ask" element={<EmbedPage />} />
          <Route element={<AppLayout />}>
            <Route path="/" element={<LandingPage />} />
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/chat/:sessionId" element={<ChatPage />} />
            <Route path="/nightwatch" element={<NightwatchPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </AuthGate>
    </BrowserRouter>
  );
}
