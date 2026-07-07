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

// Branding công khai (không cần đăng nhập — Login cần trước khi có token).
export async function getBranding() {
  return handle(await fetch(`/v1/settings/branding`));
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

// ── Cấu hình tổ chức (site_config) — admin ──────────────────────────────────
export async function getSiteConfig() {
  return handle(await fetch(`/v1/settings/site`, { headers: headers() }));
}
export async function updateSiteConfig(body, confirm = false) {
  const p = confirm ? "?confirm=true" : "";
  return handle(await fetch(`/v1/settings/site${p}`, {
    method: "PATCH", headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  }));
}
export async function uploadLogo(file) {
  const form = new FormData();
  form.append("file", file);
  return handle(await fetch(`/v1/settings/logo`, { method: "POST", headers: headers(), body: form }));
}
export async function getSettingsStatus() {
  return handle(await fetch(`/v1/settings/status`, { headers: headers() }));
}

// ── S3 connections (nguồn/đích) — admin ─────────────────────────────────────
export async function getS3Connections(role) {
  const p = role ? `?role=${encodeURIComponent(role)}` : "";
  return handle(await fetch(`/v1/s3-connections${p}`, { headers: headers() }));
}
export async function createS3Connection(body) {
  return handle(await fetch(`/v1/s3-connections`, {
    method: "POST", headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  }));
}
export async function updateS3Connection(id, body) {
  return handle(await fetch(`/v1/s3-connections/${encodeURIComponent(id)}`, {
    method: "PATCH", headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  }));
}
export async function deleteS3Connection(id, force = false) {
  const p = force ? "?force=true" : "";
  return handle(await fetch(`/v1/s3-connections/${encodeURIComponent(id)}${p}`, {
    method: "DELETE", headers: headers(),
  }));
}
export async function testS3ConnectionDraft(body) {
  return handle(await fetch(`/v1/s3-connections/test`, {
    method: "POST", headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  }));
}
export async function testS3Connection(id) {
  return handle(await fetch(`/v1/s3-connections/${encodeURIComponent(id)}/test`, {
    method: "POST", headers: headers(),
  }));
}

// ── Duyệt + import từ kho MinIO nguồn ────────────────────────────────────────
export async function getBrowseSources() {
  return handle(await fetch(`/v1/browse/sources`, { headers: headers() }));
}
export async function browseMinio(sourceId, prefix = "", token = null) {
  const p = new URLSearchParams();
  if (prefix) p.set("prefix", prefix);
  if (token) p.set("token", token);
  return handle(await fetch(`/v1/browse/${encodeURIComponent(sourceId)}?${p.toString()}`,
    { headers: headers() }));
}
export async function importFromMinio(sourceId, body) {
  return handle(await fetch(`/v1/browse/${encodeURIComponent(sourceId)}/import`, {
    method: "POST", headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  }));
}
// Tiến độ import (x/y) của 1 thư mục — duyệt đệ quy phía server, có giới hạn an toàn.
export async function browseFolderProgress(sourceId, prefix = "") {
  const p = new URLSearchParams();
  if (prefix) p.set("prefix", prefix);
  return handle(await fetch(`/v1/browse/${encodeURIComponent(sourceId)}/progress?${p.toString()}`,
    { headers: headers() }));
}

// ── Audit log (admin) ───────────────────────────────────────────────────────
export async function getAuditLog({ action, actor, target, from, to, limit = 50, beforeId } = {}) {
  const p = new URLSearchParams();
  if (action) p.set("action", action);
  if (actor) p.set("actor", actor);
  if (target) p.set("target", target);
  if (from) p.set("from", from);
  if (to) p.set("to", to);
  if (limit) p.set("limit", String(limit));
  if (beforeId) p.set("before_id", beforeId);
  return handle(await fetch(`/v1/audit-log?${p.toString()}`, { headers: headers() }));
}

async function handle(res) {
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch {}
    const msg = typeof detail === "string" ? detail : (detail?.message || res.statusText);
    const err = new Error(`${res.status} · ${msg}`);
    err.status = res.status;
    err.detail = detail;   // string HOẶC object có cấu trúc (409 kèm số bản ghi ảnh hưởng)
    throw err;
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
export async function listGcn({ batchId, branch, status, review, q, page = 1, pageSize = 50 } = {}) {
  const p = new URLSearchParams();
  if (batchId) p.set("batch_id", batchId);
  if (branch) p.set("branch", branch);
  if (status) p.set("status", status);
  if (review) p.set("review", review);
  if (q) p.set("q", q);
  p.set("page", String(page));
  p.set("page_size", String(pageSize));
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
export async function getStats({ batchId, branch, reviewerDays, reviewerFrom, reviewerTo } = {}) {
  const p = new URLSearchParams();
  if (batchId) p.set("batch_id", batchId);
  if (branch) p.set("branch", branch);
  if (reviewerFrom || reviewerTo) {
    if (reviewerFrom) p.set("reviewer_from", reviewerFrom);
    if (reviewerTo) p.set("reviewer_to", reviewerTo);
  } else if (reviewerDays) {
    p.set("reviewer_days", String(reviewerDays));
  }
  return handle(await fetch(`/v1/gcn/stats?${p.toString()}`, { headers: headers() }));
}

// Khung nhìn dạng hàng phẳng (đã áp hậu kiểm) — phục vụ xem/xuất/FME.
export async function listRows({ batchId, status, review, branch, page = 1, pageSize = 50 } = {}) {
  const p = new URLSearchParams();
  if (batchId) p.set("batch_id", batchId);
  if (branch) p.set("branch", branch);
  if (status) p.set("status", status);
  if (review) p.set("review", review);
  p.set("page", String(page));
  p.set("page_size", String(pageSize));
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

// Hậu kiểm đồng thời: soft-lock (mở thẳng không qua hàng chờ vẫn tránh ghi đè).
export async function claimReviewLock(id) {
  return handle(await fetch(`/v1/gcn/${id}/lock`, { method: "POST", headers: headers() }));
}
export async function heartbeatReviewLock(id) {
  return handle(await fetch(`/v1/gcn/${id}/lock/heartbeat`, { method: "POST", headers: headers() }));
}
export async function releaseReviewLock(id) {
  return handle(await fetch(`/v1/gcn/${id}/lock`, { method: "DELETE", headers: headers() }));
}

// Retry hàng loạt (dead-letter/lỗi) — chỉ 1 lô/lần, "dead" (poison) không nằm
// trong phạm vi (cần soi thủ công).
export async function retryErrors({ batchId, errorKind } = {}) {
  return handle(await fetch(`/v1/gcn/retry-errors`, {
    method: "POST", headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify({ batch_id: batchId, error_kind: errorKind || undefined }),
  }));
}

// Xuất nền (không cap dòng, không chặn request) — job chạy ở worker.
export async function createExportJob(body) {
  return handle(await fetch(`/v1/gcn/export-jobs`, {
    method: "POST", headers: headers({ "Content-Type": "application/json" }), body: JSON.stringify(body),
  }));
}
export async function getExportJob(id) {
  return handle(await fetch(`/v1/gcn/export-jobs/${id}`, { headers: headers() }));
}
export async function downloadExportJob(id) {
  const p = new URLSearchParams();
  if (auth.token) p.set("token", auth.token);
  const res = await fetch(`/v1/gcn/export-jobs/${id}/download?${p.toString()}`, { headers: headers() });
  if (!res.ok) throw new Error(`tải lỗi: ${res.status}`);
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `ai-hub-export-${id.slice(0, 8)}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}
