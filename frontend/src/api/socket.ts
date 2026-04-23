type Handler = (channel: string, data: any) => void;

let socket: WebSocket | null = null;
const handlers = new Set<Handler>();

function open(): WebSocket {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${proto}//${location.host}/ws/monitoring`);
  ws.onmessage = (e) => {
    try {
      const { channel, data } = JSON.parse(e.data);
      handlers.forEach((h) => h(channel, data));
    } catch {
      /* ignore */
    }
  };
  ws.onclose = () => {
    socket = null;
    setTimeout(() => {
      socket = open();
    }, 2000);
  };
  return ws;
}

export function subscribe(handler: Handler): () => void {
  if (!socket) socket = open();
  handlers.add(handler);
  return () => handlers.delete(handler);
}