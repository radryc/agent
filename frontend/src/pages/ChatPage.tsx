import { useEffect } from "react";
import { useParams } from "react-router-dom";
import { useChatStore } from "../stores/chatStore";
import ChatMessages from "../components/chat/ChatMessages";
import ChatInput from "../components/chat/ChatInput";

export default function ChatPage() {
  const { sessionId } = useParams();
  const { activeSessionId, switchSession } = useChatStore();

  useEffect(() => {
    if (sessionId && sessionId !== activeSessionId) {
      switchSession(sessionId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  return (
    <div className="flex flex-col h-full">
      <ChatMessages />
      <ChatInput />
    </div>
  );
}
