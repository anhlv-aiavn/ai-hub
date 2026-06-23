// SSE realtime: nhận event {type, gcn_id/batch_id, status} → đẩy tới subscriber + toast.
import { auth } from "./api.js";
import { notify } from "./toast.js";

let es = null;
const subscribers = new Set();

export function subscribeEvents(fn) {
  subscribers.add(fn);
  return () => subscribers.delete(fn);
}

export function stopEvents() {
  if (es) { es.close(); es = null; }
}

export function startEvents() {
  stopEvents();
  if (!auth.token) return;  // chưa đăng nhập → không mở SSE
  const q = new URLSearchParams();
  q.set("token", auth.token);
  es = new EventSource(`/v1/events?${q.toString()}`);
  es.onmessage = (e) => {
    try {
      const d = JSON.parse(e.data);
      subscribers.forEach((fn) => { try { fn(d); } catch { /* bỏ qua */ } });
      if (d.type === "batch" && d.status === "done") notify("Lô đã xử lý xong", "ok");
      if (d.type === "gcn" && d.status === "error") notify("Một GCN xử lý lỗi", "err");
    } catch { /* bỏ qua */ }
  };
}
