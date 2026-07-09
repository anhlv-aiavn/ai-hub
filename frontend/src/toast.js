// Trung tâm thông báo: vừa popup thoáng qua (Toaster), vừa lưu lịch sử cho chuông (Bell).
const items = []; // mới nhất đầu danh sách
const toastSubs = new Set(); // hiển thị thoáng qua
const storeSubs = new Set(); // danh sách chuông
let seq = 0;

function emitStore() {
  const snap = items.slice();
  storeSubs.forEach((f) => f(snap));
}

export function toast(message, type = "info") {
  const n = { id: ++seq, message: String(message), type, time: Date.now(), read: false };
  items.unshift(n);
  if (items.length > 60) items.pop();
  emitStore();
  toastSubs.forEach((f) => f(n));
  return n;
}
export const toastOk = (m) => toast(m, "ok");
export const toastErr = (m) => toast(m, "err");
export const toastWarn = (m) => toast(m, "warn");
export const notify = toast;

// Toaster (thoáng qua)
export function subscribe(fn) {
  toastSubs.add(fn);
  return () => toastSubs.delete(fn);
}
// Bell (lịch sử)
export function subscribeStore(fn) {
  storeSubs.add(fn);
  fn(items.slice());
  return () => storeSubs.delete(fn);
}
export function markAllRead() {
  items.forEach((i) => (i.read = true));
  emitStore();
}
export function clearAll() {
  items.length = 0;
  emitStore();
}
