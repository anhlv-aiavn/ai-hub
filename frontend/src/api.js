// Lớp mỏng gọi /v1 của AI-HUB. API key (tùy chọn — Sobagi cấp) lưu localStorage.
const KEY = "aihub.apiKey";

export const auth = {
  get apiKey() { return localStorage.getItem(KEY) || ""; },
  set apiKey(v) { localStorage.setItem(KEY, v || ""); },
};

export function headers(extra = {}) {
  const h = { ...extra };
  if (auth.apiKey) h["X-API-Key"] = auth.apiKey;
  return h;
}

async function handle(res) {
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(`${res.status} · ${detail}`);
  }
  return res.json();
}

// ── Lô ──────────────────────────────────────────────────────────────────────
// Upload qua XHR để có tiến độ thực (file nặng không còn "treo" vô hình).
// onProgress(pct 0..100, loadedBytes, totalBytes).
export function createBatch({ files, name, onProgress }) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    if (name) form.append("name", name);
    for (const f of files) form.append("files", f);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/v1/batches");
    if (auth.apiKey) xhr.setRequestHeader("X-API-Key", auth.apiKey);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.(Math.round((e.loaded / e.total) * 100), e.loaded, e.total);
    };
    xhr.onload = () => {
      let body = null;
      try { body = JSON.parse(xhr.responseText); } catch { /* ignore */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else reject(new Error(`${xhr.status} · ${(body && body.detail) || xhr.statusText}`));
    };
    xhr.onerror = () => reject(new Error("Lỗi mạng khi tải lên"));
    xhr.send(form);
  });
}
export async function listBatches(limit = 50) {
  return handle(await fetch(`/v1/batches?limit=${limit}`, { headers: headers() }));
}
export async function getBatch(id) {
  return handle(await fetch(`/v1/batches/${id}`, { headers: headers() }));
}

// ── GCN / bảng trích xuất ────────────────────────────────────────────────────
export async function listGcn({ batchId, status, review, q, limit = 500 } = {}) {
  const p = new URLSearchParams();
  if (batchId) p.set("batch_id", batchId);
  if (status) p.set("status", status);
  if (review) p.set("review", review);
  if (q) p.set("q", q);
  if (limit) p.set("limit", String(limit));
  return handle(await fetch(`/v1/gcn?${p.toString()}`, { headers: headers() }));
}
export async function getGcn(id) {
  return handle(await fetch(`/v1/gcn/${id}`, { headers: headers() }));
}
export async function getPageInfo(id) {
  return handle(await fetch(`/v1/gcn/${id}/pageinfo`, { headers: headers() }));
}
export function pageImageUrl(gcnId, n, w = 1100) {
  const p = new URLSearchParams({ w: String(w) });
  if (auth.apiKey) p.set("api_key", auth.apiKey);
  return `/v1/gcn/${gcnId}/page/${n}?${p.toString()}`;
}

// File cắt (PDF đã xoay thẳng) — preview riêng theo chỉ số cắt.
export async function getCutPageInfo(gcnId, ci) {
  return handle(await fetch(`/v1/gcn/${gcnId}/cut/${ci}/pageinfo`, { headers: headers() }));
}
export function cutPageImageUrl(gcnId, ci, n, w = 1100) {
  const p = new URLSearchParams({ w: String(w) });
  if (auth.apiKey) p.set("api_key", auth.apiKey);
  return `/v1/gcn/${gcnId}/cut/${ci}/page/${n}?${p.toString()}`;
}

// Khung nhìn dạng hàng phẳng (đã áp hậu kiểm) — phục vụ xem/xuất/FME.
export async function listRows({ batchId, status, review } = {}) {
  const p = new URLSearchParams();
  if (batchId) p.set("batch_id", batchId);
  if (status) p.set("status", status);
  if (review) p.set("review", review);
  return handle(await fetch(`/v1/gcn/rows?${p.toString()}`, { headers: headers() }));
}

// Tải CSV (BOM UTF-8) theo bộ lọc hiện tại.
export async function downloadCsv({ batchId, status, review } = {}) {
  const p = new URLSearchParams();
  if (batchId) p.set("batch_id", batchId);
  if (status) p.set("status", status);
  if (review) p.set("review", review);
  if (auth.apiKey) p.set("api_key", auth.apiKey);
  const res = await fetch(`/v1/gcn/export.csv?${p.toString()}`, { headers: headers() });
  if (!res.ok) throw new Error(`xuất CSV lỗi: ${res.status}`);
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "ai-hub-export.csv";
  a.click();
  URL.revokeObjectURL(a.href);
}

// Hậu kiểm: ghi review (đặt tên / overrides / trạng thái).
export async function putReview(id, body) {
  return handle(await fetch(`/v1/gcn/${id}`, {
    method: "PUT", headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  }));
}

// Tải bộ đã gom (zip PDF + JSON) qua proxy + header → blob → save.
export async function downloadGcn(id, filename) {
  const p = new URLSearchParams();
  if (auth.apiKey) p.set("api_key", auth.apiKey);
  const res = await fetch(`/v1/gcn/${id}/download?${p.toString()}`, { headers: headers() });
  if (!res.ok) throw new Error(`tải lỗi: ${res.status}`);
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename || `${id}.zip`;
  a.click();
  URL.revokeObjectURL(a.href);
}
