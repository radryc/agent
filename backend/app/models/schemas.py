from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class MessageBase(BaseModel):
    role: str  # "user" | "assistant" | "system"
    content: str


class Message(MessageBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SessionBase(BaseModel):
    title: str = "New Chat"


class Session(SessionBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    messages: list[Message] = []


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    message: Message
    session_id: str


class SessionUpdate(BaseModel):
    title: str


class AgentInfo(BaseModel):
    name: str
    system_prompt: str
    model: str = "gemma-4-E4B-it-GGUF (Q4_K_M)"


class HealthResponse(BaseModel):
    status: str
    llm: str
    version: str = "0.1.0"


# WebSocket message protocol
class WSMessage(BaseModel):
    type: str  # "message" | "token" | "done" | "error" | "ping" | "pong"
    data: dict | str | None = None
    session_id: str | None = None
