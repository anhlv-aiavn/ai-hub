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

// Dịch lỗi kỹ thuật (litellm/OpenAI, network...) lưu thô trong doc/job.error
// thành thông báo tiếng Việt dễ hiểu cho người dùng cuối. Khớp theo từ khóa vì
// message gốc không có mã lỗi chuẩn hoá — chỉ là chuỗi str(exception) từ backend.
export function friendlyError(raw) {
  if (!raw) return raw;
  const low = String(raw).toLowerCase();
  if (low.includes("timeout")) return "Hệ thống nhận diện phản hồi quá lâu, vui lòng thử lại.";
  if (low.includes("connection error") || low.includes("connecterror") || low.includes("econnrefused"))
    return "Không kết nối được tới hệ thống nhận diện (VLM). Vui lòng thử lại sau ít phút.";
  if (low.includes("ratelimiterror") || low.includes("rate limit"))
    return "Hệ thống nhận diện đang quá tải, vui lòng thử lại sau.";
  if (low.includes("authenticationerror") || low.includes("api key") || low.includes("api_key"))
    return "Lỗi xác thực với hệ thống nhận diện — liên hệ quản trị viên.";
  if (low.includes("litellm") || low.includes("openaiexception") || low.includes("internalservererror") || low.includes("apierror"))
    return "Hệ thống nhận diện gặp sự cố nội bộ, vui lòng thử lại sau.";
  return raw;
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
// Đăng xuất chủ động: báo backend giải phóng session_id NGAY (không thì tài
// khoản bị coi là "đang hoạt động" tới hết SESSION_ACTIVE_TTL, chặn nhầm lượt
// đăng nhập kế tiếp) — bắn đi rồi xóa token cục bộ ngay, không chờ phản hồi.
// Tự đổi mật khẩu của chính mình (mọi vai trò) — khác PATCH /v1/users/{username}
// vốn chỉ admin gọi được để đổi cho người khác.
export async function changeMyPassword(oldPassword, newPassword) {
  return handle(await fetch(`/v1/auth/change-password`, {
    method: "POST", headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  }));
}
export function logout() {
  const token = auth.token;
  auth.token = "";
  if (token) {
    fetch(`/v1/auth/logout`, { method: "POST", headers: { Authorization: `Bearer ${token}` } }).catch(() => {});
  }
}

// Nhịp tim phiên đăng nhập — FE gọi định kỳ khi tab đang mở (nuôi "đang hoạt
// động"); admin thấy trạng thái này ở Users.jsx.
export async function heartbeat() {
  return handle(await fetch(`/v1/auth/heartbeat`, { method: "POST", headers: headers() }));
}
// Đánh dấu "không hoạt động" ngay khi rời tab. Gọi qua navigator.sendBeacon
// (không set header được) nên truyền token qua query, không đi qua handle().
export function sendSessionEndBeacon() {
  if (!auth.token) return;
  const url = `/v1/auth/session-end?token=${encodeURIComponent(auth.token)}`;
  navigator.sendBeacon?.(url);
}

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
export async function forceLogoutUser(username) {
  return handle(await fetch(`/v1/users/${encodeURIComponent(username)}/force-logout`, {
    method: "POST", headers: headers(),
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
// `purpose`: "gcn" (mặc định, pipeline chính, singleton) | "qc" (QC Sync, nhiều bản ghi).
export async function getS3Connections(role, purpose) {
  const p = new URLSearchParams();
  if (role) p.set("role", role);
  if (purpose) p.set("purpose", purpose);
  const qs = p.toString();
  return handle(await fetch(`/v1/s3-connections${qs ? `?${qs}` : ""}`, { headers: headers() }));
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

// ── Duyệt + import từ kho S3 nguồn ────────────────────────────────────────
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
export async function getAuditLogActors() {
  return handle(await fetch(`/v1/audit-log/actors`, { headers: headers() }));
}
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
function uploadOne({ file, name, batchId, onBytes }) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    if (name) form.append("name", name);
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
export async function createBatch({ files, name, onProgress }) {
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
export async function listBatches(limit = 50) {
  return handle(await fetch(`/v1/batches?limit=${limit}`, { headers: headers() }));
}
export async function getBatch(id) {
  return handle(await fetch(`/v1/batches/${id}`, { headers: headers() }));
}
export async function deleteBatch(id) {
  return handle(await fetch(`/v1/batches/${id}`, { method: "DELETE", headers: headers() }));
}

// ── Gán user ↔ lô (admin) — đối xứng với updateUser({assignedBatchIds}) ────
export async function getBatchUsers(batchId) {
  return handle(await fetch(`/v1/batches/${encodeURIComponent(batchId)}/users`, { headers: headers() }));
}
export async function assignBatchUser(batchId, username) {
  return handle(await fetch(`/v1/batches/${encodeURIComponent(batchId)}/users`, {
    method: "POST", headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify({ username }),
  }));
}
export async function unassignBatchUser(batchId, username) {
  return handle(await fetch(
    `/v1/batches/${encodeURIComponent(batchId)}/users/${encodeURIComponent(username)}`,
    { method: "DELETE", headers: headers() },
  ));
}

// ── GCN / bảng trích xuất ────────────────────────────────────────────────────
export async function listGcn({ batchId, status, review, reviewer, q, canhBao, page = 1, pageSize = 50 } = {}) {
  const p = new URLSearchParams();
  if (batchId) p.set("batch_id", batchId);
  if (status) p.set("status", status);
  if (review) p.set("review", review);
  if (reviewer) p.set("reviewer", reviewer);
  if (q) p.set("q", q);
  if (canhBao) p.set("canh_bao", canhBao === "co" ? "true" : "false");
  p.set("page", String(page));
  p.set("page_size", String(pageSize));
  return handle(await fetch(`/v1/gcn?${p.toString()}`, { headers: headers() }));
}
export async function getGcn(id) {
  return handle(await fetch(`/v1/gcn/${id}`, { headers: headers() }));
}
export async function deleteGcn(id) {
  return handle(await fetch(`/v1/gcn/${id}`, { method: "DELETE", headers: headers() }));
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
export async function getStats({ batchId, reviewerDays, reviewerFrom, reviewerTo } = {}) {
  const p = new URLSearchParams();
  if (batchId) p.set("batch_id", batchId);
  if (reviewerFrom || reviewerTo) {
    if (reviewerFrom) p.set("reviewer_from", reviewerFrom);
    if (reviewerTo) p.set("reviewer_to", reviewerTo);
  } else if (reviewerDays) {
    p.set("reviewer_days", String(reviewerDays));
  }
  return handle(await fetch(`/v1/gcn/stats?${p.toString()}`, { headers: headers() }));
}

// Khung nhìn dạng hàng phẳng (đã áp hậu kiểm) — phục vụ xem/xuất/FME.
export async function listRows({ batchId, status, review, page = 1, pageSize = 50 } = {}) {
  const p = new URLSearchParams();
  if (batchId) p.set("batch_id", batchId);
  if (status) p.set("status", status);
  if (review) p.set("review", review);
  p.set("page", String(page));
  p.set("page_size", String(pageSize));
  return handle(await fetch(`/v1/gcn/rows?${p.toString()}`, { headers: headers() }));
}

// Tải CSV (BOM UTF-8) theo bộ lọc hiện tại.
export async function downloadCsv({ batchId, status, review } = {}) {
  const p = new URLSearchParams();
  if (batchId) p.set("batch_id", batchId);
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

// Retry hàng loạt (dead-letter/lỗi). batchId rỗng ⇒ MỌI LÔ (cần admin). since/until
// (ISO 8601) lọc theo thời điểm lỗi. "dead" (poison) không nằm trong phạm vi.
export async function retryErrors({ batchId, errorKind, since, until } = {}) {
  return handle(await fetch(`/v1/gcn/retry-errors`, {
    method: "POST", headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      batch_id: batchId || undefined,
      error_kind: errorKind || undefined,
      since: since || undefined,
      until: until || undefined,
    }),
  }));
}

// Giải phóng hồ sơ kẹt ở "processing" → "queued" (sau khi restart/build worker).
// batchId rỗng ⇒ MỌI LÔ (cần admin). minStaleSeconds: chỉ đụng doc cũ hơn ngưỡng.
export async function releaseStuck({ batchId, minStaleSeconds } = {}) {
  return handle(await fetch(`/v1/gcn/release-stuck`, {
    method: "POST", headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      batch_id: batchId || undefined,
      min_stale_seconds: minStaleSeconds ?? undefined,
    }),
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
// ── QC Sync (pipeline mới: MinIO nguồn → QC scanner → OCR → crop GCN) — admin
export async function getQcSyncConfigs() {
  return handle(await fetch(`/v1/qc-sync/configs`, { headers: headers() }));
}
export async function createQcSyncConfig(body) {
  return handle(await fetch(`/v1/qc-sync/configs`, {
    method: "POST", headers: headers({ "Content-Type": "application/json" }), body: JSON.stringify(body),
  }));
}
export async function updateQcSyncConfig(id, body) {
  return handle(await fetch(`/v1/qc-sync/configs/${encodeURIComponent(id)}`, {
    method: "PATCH", headers: headers({ "Content-Type": "application/json" }), body: JSON.stringify(body),
  }));
}
export async function deleteQcSyncConfig(id) {
  return handle(await fetch(`/v1/qc-sync/configs/${encodeURIComponent(id)}`, {
    method: "DELETE", headers: headers(),
  }));
}
export async function runQcSyncNow(id) {
  return handle(await fetch(`/v1/qc-sync/configs/${encodeURIComponent(id)}/run`, {
    method: "POST", headers: headers(),
  }));
}
// Lượt quét (qc_sync_job) đang queued/processing của 1 kênh, nếu có — poll để
// hiện tiến độ (scanned/enqueued/skipped) thay vì chỉ thấy lỗi 409.
export async function getQcSyncActiveJob(configId) {
  return handle(await fetch(`/v1/qc-sync/configs/${encodeURIComponent(configId)}/active-job`, { headers: headers() }));
}
export async function cancelQcSyncJob(jobId) {
  return handle(await fetch(`/v1/qc-sync/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST", headers: headers(),
  }));
}
export async function getQcSyncStats({ configId, range = "all" } = {}) {
  const p = new URLSearchParams({ range });
  if (configId) p.set("config_id", configId);
  return handle(await fetch(`/v1/qc-sync/stats?${p.toString()}`, { headers: headers() }));
}
export async function getQcSyncItems({
  configId, status, verdict, processedOnly, sortBy, page = 1, pageSize = 50,
} = {}) {
  const p = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (configId) p.set("config_id", configId);
  if (status) p.set("status", status);
  if (verdict) p.set("verdict", verdict);
  if (processedOnly) p.set("processed_only", "true");
  if (sortBy) p.set("sort_by", sortBy);
  return handle(await fetch(`/v1/qc-sync/items?${p.toString()}`, { headers: headers() }));
}
export async function retryQcSyncItem(itemId) {
  return handle(await fetch(`/v1/qc-sync/items/${encodeURIComponent(itemId)}/retry`, {
    method: "POST", headers: headers(),
  }));
}
export async function deleteQcSyncItem(itemId) {
  return handle(await fetch(`/v1/qc-sync/items/${encodeURIComponent(itemId)}`, {
    method: "DELETE", headers: headers(),
  }));
}
// Xóa TOÀN BỘ lịch sử quét (mọi file) của 1 kênh — quét lại từ đầu cả thư mục.
export async function clearQcSyncConfigItems(configId) {
  return handle(await fetch(`/v1/qc-sync/configs/${encodeURIComponent(configId)}/items`, {
    method: "DELETE", headers: headers(),
  }));
}
// Mở trực tiếp PDF nguồn (tab mới) — token qua query vì đây là link, không phải fetch().
export function qcSyncSourcePdfUrl(itemId) {
  const p = new URLSearchParams();
  if (auth.token) p.set("token", auth.token);
  return `/v1/qc-sync/items/${encodeURIComponent(itemId)}/source-pdf?${p.toString()}`;
}
// Mở trực tiếp 1 file ĐÃ CẮT (khác PDF nguồn — nằm ở MinIO đích riêng của QC Sync).
export function qcSyncCutPdfUrl(itemId, cutIndex) {
  const p = new URLSearchParams();
  if (auth.token) p.set("token", auth.token);
  return `/v1/qc-sync/items/${encodeURIComponent(itemId)}/cuts/${cutIndex}/pdf?${p.toString()}`;
}
// Chuỗi thời gian theo ngày (qc_stats_daily) cho 3 biểu đồ ở trang Tổng quan
// (QcSyncStats.jsx) — khác getQcSyncStats (chỉ trả 1 tổng gộp cho 1 khoảng).
export async function getQcSyncStatsSeries({ configId, days = 90 } = {}) {
  const p = new URLSearchParams({ days: String(days) });
  if (configId) p.set("config_id", configId);
  return handle(await fetch(`/v1/qc-sync/stats/series?${p.toString()}`, { headers: headers() }));
}
// Đếm theo TỪNG kênh (= từng Phường/Xã) cho 1 khung — bảng "Theo Phường/Xã" ở Tổng quan.
export async function getQcSyncStatsByConfig(range = "all") {
  return handle(await fetch(`/v1/qc-sync/stats/by-config?range=${encodeURIComponent(range)}`, { headers: headers() }));
}
// Danh sách Phường/Xã đã đồng bộ (dùng để hiện tên thay mã kênh).
export async function getQcWards() {
  return handle(await fetch(`/v1/qc-sync/wards`, { headers: headers() }));
}
// Đồng bộ danh sách Phường/Xã từ 1 API ngoài (URL admin tự nhập) — GHI ĐÈ toàn bộ danh sách cũ.
// `extraHeaders`: object {name: value} — vd { Cookie: "..." } cho API cần đăng
// nhập qua session thay vì Bearer token đơn giản.
export async function syncQcWards(url, extraHeaders) {
  return handle(await fetch(`/v1/qc-sync/wards/sync`, {
    method: "POST", headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify({ url, headers: extraHeaders && Object.keys(extraHeaders).length ? extraHeaders : undefined }),
  }));
}

// ── Phân loại hồ sơ + "làm mịn dữ liệu" (phase 2 QC Sync, tab "Phân loại") ──
export async function getQcClassifications({ configId, q, structuralLabel, loaiGiay, page = 1, pageSize = 50 } = {}) {
  const p = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (configId) p.set("config_id", configId);
  if (q) p.set("q", q);
  if (structuralLabel) p.set("structural_label", structuralLabel);
  if (loaiGiay) p.set("loai_giay", loaiGiay);
  return handle(await fetch(`/v1/qc-sync/classifications?${p.toString()}`, { headers: headers() }));
}
export async function reclassifyQcCut(itemId, cutIndex) {
  return handle(await fetch(`/v1/qc-sync/items/${encodeURIComponent(itemId)}/cuts/${cutIndex}/reclassify`, {
    method: "POST", headers: headers(),
  }));
}
export async function getQcRefinedPayload(itemId, cutIndex) {
  return handle(await fetch(`/v1/qc-sync/items/${encodeURIComponent(itemId)}/cuts/${cutIndex}/refined-payload`, {
    headers: headers(),
  }));
}
// Phân loại/làm mịn dữ liệu cho các item ĐÃ XONG từ TRƯỚC khi tính năng này ra
// đời — xử lý tối đa `limit` item/lần gọi, trả `has_more` để gọi lặp lại.
export async function backfillQcClassifications({ configId, limit = 300 } = {}) {
  const p = new URLSearchParams({ limit: String(limit) });
  if (configId) p.set("config_id", configId);
  return handle(await fetch(`/v1/qc-sync/classifications/backfill?${p.toString()}`, {
    method: "POST", headers: headers(),
  }));
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
