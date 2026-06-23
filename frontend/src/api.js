// Lớp mỏng gọi /v1 của AI-HUB. Token JWT (đăng nhập) lưu localStorage.
const TOKEN_KEY = "aihub.token";

export const auth = {
  get token() { return localStorage.getItem(TOKEN_KEY) || ""; },
  set token(v) { v ? localStorage.setItem(TOKEN_KEY, v) : localStorage.removeItem(TOKEN_KEY); },
};

export function headers(extra = {}) {
  const h = { ...extra };
  if (auth.token) h["Authorization"] = `Bearer ${auth.token}`;
  return h;
}

// Tài khoản
export async function login(username, password) {
  const res = await fetch(`/v1/auth/login`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const data = await handle(res);
  auth.token = data.token;
  return data.user;
}
export async function getMe() {
  const d = await handle(await fetch(`/v1/auth/me`, { headers: headers() }));
  return d.user;
}
export function logout() { auth.token = ""; }

// Quản trị tài khoản (admin)
export async function listUsers() {
  return handle(await fetch(`/v1/users`, { headers: headers() }));
}
export async function createUser(body) {
  return handle(await fetch(`/v1/users`, {
    method: "POST", headers: headers({ "Content-Type": "application/json" }), body: JSON.stringify(body),
  }));
}
export async function updateUser(username, body) {
  return handle(await fetch(`/v1/users/${encodeURIComponent(username)}`, {
    method: "PATCH", headers: headers({ "Content-Type": "application/json" }), body: JSON.stringify(body),
  }));
}
export async function deleteUser(username) {
  return handle(await fetch(`/v1/users/${encodeURIComponent(username)}`, {
    method: "DELETE", headers: headers(),
  }));
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
// Upload 1 file (XHR để có tiến độ byte thực). batchId rỗng = tạo lô mới.
function uploadOne({ file, name, branch, batchId, onBytes }) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    if (name) form.append("name", name);
    if (branch) form.append("branch", branch);
    if (batchId) form.append("batch_id", batchId);
    form.append("files", file);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/v1/batches");
    if (auth.token) xhr.setRequestHeader("Authorization", `Bearer ${auth.token}`);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onBytes?.(e.loaded); };
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

// Upload TỪNG file một (cùng batch_id) → né giới hạn body nginx khi lô nặng, file
// lỗi không kéo đổ cả lô. onProgress(pct 0..100, {index, count, name}).
export async function createBatch({ files, name, branch, onProgress }) {
  const list = Array.from(files || []);
  const total = list.reduce((s, f) => s + (f.size || 0), 0) || 1;
  let doneBytes = 0;
  let batchId = null;
  let fileCount = 0;
  const failed = [];

  for (let i = 0; i < list.length; i++) {
    const f = list[i];
    onProgress?.(Math.round((doneBytes / total) * 100), { index: i + 1, count: list.length, name: f.name });
    try {
      const res = await uploadOne({
        file: f,
        name: batchId ? undefined : (name || undefined),
        branch: batchId ? undefined : (branch || undefined),
        batchId,
        onBytes: (loaded) => onProgress?.(
          Math.round(((doneBytes + loaded) / total) * 100),
          { index: i + 1, count: list.length, name: f.name },
        ),
      });
      batchId = res.batch_id;
      fileCount = res.file_count;
    } catch (e) {
      failed.push(f.name);
    }
    doneBytes += f.size || 0;
  }

  if (!batchId) throw new Error(failed.length ? `Tải lên thất bại: ${failed.join(", ")}` : "Không có tệp hợp lệ");
  onProgress?.(100, { index: list.length, count: list.length });
  return { batch_id: batchId, file_count: fileCount, failed };
}
export async function getBranches() {
  return handle(await fetch(`/v1/batches/branches`, { headers: headers() }));
}
export async function listBatches(limit = 50) {
  return handle(await fetch(`/v1/batches?limit=${limit}`, { headers: headers() }));
}
export async function getBatch(id) {
  return handle(await fetch(`/v1/batches/${id}`, { headers: headers() }));
}

// ── GCN / bảng trích xuất ────────────────────────────────────────────────────
export async function listGcn({ batchId, branch, status, review, q, limit = 500 } = {}) {
  const p = new URLSearchParams();
  if (batchId) p.set("batch_id", batchId);
  if (branch) p.set("branch", branch);
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
  if (auth.token) p.set("token", auth.token);
  return `/v1/gcn/${gcnId}/page/${n}?${p.toString()}`;
}

// File cắt (PDF đã xoay thẳng) — preview riêng theo chỉ số cắt.
export async function getCutPageInfo(gcnId, ci) {
  return handle(await fetch(`/v1/gcn/${gcnId}/cut/${ci}/pageinfo`, { headers: headers() }));
}
export function cutPageImageUrl(gcnId, ci, n, w = 1100) {
  const p = new URLSearchParams({ w: String(w) });
  if (auth.token) p.set("token", auth.token);
  return `/v1/gcn/${gcnId}/cut/${ci}/page/${n}?${p.toString()}`;
}

// Thống kê tổng hợp (KPI + breakdown + cảnh báo) cho bảng Thống kê.
export async function getStats({ batchId, branch } = {}) {
  const p = new URLSearchParams();
  if (batchId) p.set("batch_id", batchId);
  if (branch) p.set("branch", branch);
  return handle(await fetch(`/v1/gcn/stats?${p.toString()}`, { headers: headers() }));
}

// Khung nhìn dạng hàng phẳng (đã áp hậu kiểm) — phục vụ xem/xuất/FME.
export async function listRows({ batchId, status, review, branch } = {}) {
  const p = new URLSearchParams();
  if (batchId) p.set("batch_id", batchId);
  if (branch) p.set("branch", branch);
  if (status) p.set("status", status);
  if (review) p.set("review", review);
  return handle(await fetch(`/v1/gcn/rows?${p.toString()}`, { headers: headers() }));
}

// Tải CSV (BOM UTF-8) theo bộ lọc hiện tại.
export async function downloadCsv({ batchId, status, review, branch } = {}) {
  const p = new URLSearchParams();
  if (batchId) p.set("batch_id", batchId);
  if (branch) p.set("branch", branch);
  if (status) p.set("status", status);
  if (review) p.set("review", review);
  if (auth.token) p.set("token", auth.token);
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
  if (auth.token) p.set("token", auth.token);
  const res = await fetch(`/v1/gcn/${id}/download?${p.toString()}`, { headers: headers() });
  if (!res.ok) throw new Error(`tải lỗi: ${res.status}`);
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename || `${id}.zip`;
  a.click();
  URL.revokeObjectURL(a.href);
}
