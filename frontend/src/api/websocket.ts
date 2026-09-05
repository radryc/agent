import type { WSIncoming, WSOutgoing } from "../types";

type MessageHandler = (msg: WSIncoming) => void;
type SocketEventHandler = () => void;

interface SocketEvents {
  onOpen?: SocketEventHandler;
  onClose?: SocketEventHandler;
  onError?: SocketEventHandler;
}

export class ChatWebSocket {
  private ws: WebSocket | null = null;
  private url: string;
  private handler: MessageHandler;
  private events: SocketEvents;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private sendQueue: WSOutgoing[] = [];
  private shouldReconnect = true;

  constructor(handler: MessageHandler, events: SocketEvents = {}) {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    this.url = `${proto}//${window.location.host}/ws/chat`;
    this.handler = handler;
    this.events = events;
  }

  connect(): void {
    if (
      this.ws?.readyState === WebSocket.OPEN ||
      this.ws?.readyState === WebSocket.CONNECTING
    ) {
      return;
    }

    this.shouldReconnect = true;
    this.ws = new WebSocket(this.url);

    this.ws.onopen = () => {
      this.startHeartbeat();
      this.flushQueue();
      this.events.onOpen?.();
    };

    this.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as WSIncoming;
        this.handler(msg);
      } catch {
        // ignore malformed
      }
    };

    this.ws.onclose = () => {
      this.stopHeartbeat();
      this.events.onClose?.();
      if (this.shouldReconnect) {
        this.reconnectTimer = setTimeout(() => this.connect(), 2000);
      }
    };

    this.ws.onerror = () => {
      this.events.onError?.();
      this.ws?.close();
    };
  }

  send(msg: WSOutgoing): boolean {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
      return true;
    }

    if (this.ws?.readyState === WebSocket.CONNECTING) {
      this.sendQueue.push(msg);
      return true;
    }

    if (this.shouldReconnect) {
      this.sendQueue.push(msg);
      this.connect();
      return true;
    }

    return false;
  }

  disconnect(): void {
    this.shouldReconnect = false;
    this.stopHeartbeat();
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.ws?.close();
    this.ws = null;
  }

  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  private startHeartbeat(): void {
    this.heartbeatTimer = setInterval(() => {
      this.send({ type: "ping" });
    }, 30000);
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  private flushQueue(): void {
    if (this.ws?.readyState !== WebSocket.OPEN || this.sendQueue.length === 0) {
      return;
    }

    const queued = [...this.sendQueue];
    this.sendQueue = [];
    for (const msg of queued) {
      this.ws.send(JSON.stringify(msg));
    }
  }
}
