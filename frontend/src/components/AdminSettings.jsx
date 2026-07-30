import React, { useEffect, useState } from "react";
import Icon from "./Icon.jsx";
import Modal from "./Modal.jsx";
import {
  getSiteConfig, updateSiteConfig, uploadLogo, getSettingsStatus,
  getS3Connections, createS3Connection, updateS3Connection, deleteS3Connection,
  testS3Connection, testS3ConnectionDraft,
  listBatches, retryErrors, releaseStuck,
} from "../api.js";
import { toastOk, toastErr } from "../toast.js";
import { copyToClipboard } from "../clipboard.js";

const TABS = [
  ["org", "Tổ chức"],
  ["source", "S3 nguồn"],
  ["dest", "S3 đích"],
  ["errors", "Dead-letter/lỗi"],
];

// Modal cấu hình hệ thống (admin-only) — tabs. Mở từ AccountMenu, cạnh "Quản trị
// tài khoản". Pattern modal theo Users.jsx (panel + export-head + nút đóng).
export default function AdminSettings({ onClose }) {
  const [tab, setTab] = useState("org");
  return (
    <Modal title="Cấu hình hệ thống" onClose={onClose} wide>
      <div className="admin-settings">
        <nav className="admin-tabs">
          {TABS.map(([k, label]) => (
            <button key={k} className={k === tab ? "tab active" : "tab"} onClick={() => setTab(k)}>{label}</button>
          ))}
        </nav>
        {tab === "org" && <OrgTab />}
        {tab === "source" && <S3Tab role="source" />}
        {tab === "dest" && <S3Tab role="destination" />}
        {tab === "errors" && <ErrorsTab />}
      </div>
    </Modal>
  );
}

// ── Tab: Tổ chức (branding) ──────────────────────────────────────────────────
function OrgTab() {
  const [cfg, setCfg] = useState(null);
  const [busy, setBusy] = useState(false);
  const [destConfigured, setDestConfigured] = useState(true); // lạc quan khi đang tải, tránh nháy disable rồi lại bật
  const [logoBusy, setLogoBusy] = useState(false);
  const logoInputRef = React.useRef(null);

  async function refresh() {
    try { setCfg(await getSiteConfig()); } catch (e) { toastErr(e.message || e); }
  }
  useEffect(() => {
    refresh();
    getSettingsStatus().then((s) => setDestConfigured(!!s.destination_configured)).catch(() => {});
  }, []);

  async function onLogoFileChange(e) {
    const file = e.target.files?.[0];
    e.target.value = ""; // cho phép chọn lại đúng file đó lần sau
    if (!file) return;
    setLogoBusy(true);
    try {
      const after = await uploadLogo(file);
      setCfg((prev) => (prev ? { ...prev, branding: after.branding } : prev));
      toastOk("Đã cập nhật logo");
    } catch (e2) { toastErr(e2.message || e2); } finally { setLogoBusy(false); }
  }

  function setField(path, value) {
    setCfg((prev) => {
      const next = { ...prev };
      if (path[0] === "branding") next.branding = { ...next.branding, [path[1]]: value };
      else next[path[0]] = value;
      return next;
    });
  }

  async function save() {
    setBusy(true);
    try {
      await updateSiteConfig({ name: cfg.name, branding: cfg.branding });
      toastOk("Đã lưu cấu hình tổ chức");
      refresh();
    } catch (e) {
      toastErr(e.message || e);
    } finally { setBusy(false); }
  }

  if (!cfg) return <div className="muted" style={{ padding: 16 }}>Đang tải…</div>;

  return (
    <div className="admin-tab-body">
      <label className="field-label" htmlFor="org-logo">Logo (URL)</label>
      <div className="logo-edit-row">
        <input id="org-logo" className="text-input" value={cfg.branding?.logo_url || ""}
          onChange={(e) => setField(["branding", "logo_url"], e.target.value)} />
        {cfg.branding?.logo_url && (
          <img className="logo-preview" src={cfg.branding.logo_url} alt="Logo hiện tại"
            onError={(e) => { e.currentTarget.style.visibility = "hidden"; }} />
        )}
        <input ref={logoInputRef} type="file" accept="image/png,image/jpeg,image/webp"
          style={{ display: "none" }} onChange={onLogoFileChange} />
        <button type="button" className="ghost sm" disabled={logoBusy || !destConfigured}
          title={!destConfigured ? "Cần cấu hình S3 đích trước khi tải logo lên" : undefined}
          onClick={() => logoInputRef.current?.click()}>
          <Icon name="upload" size={13} /> {logoBusy ? "Đang tải…" : "Tải ảnh lên"}
        </button>
      </div>

      <label className="field-label" htmlFor="org-brand">Tên hiển thị (branding)</label>
      <input id="org-brand" className="text-input" value={cfg.branding?.org_name || ""}
        onChange={(e) => setField(["branding", "org_name"], e.target.value)} />

      <label className="field-label" htmlFor="org-copy">Copyright</label>
      <input id="org-copy" className="text-input" value={cfg.branding?.copyright_text || ""}
        onChange={(e) => setField(["branding", "copyright_text"], e.target.value)} />

      <div className="admin-tab-foot">
        <button className="primary" disabled={busy} onClick={save}>
          {busy ? "Đang lưu…" : "Lưu thay đổi"}
        </button>
      </div>
    </div>
  );
}

// ── Tab: S3 nguồn / S3 đích ──────────────────────────────────────────────────
const EMPTY_CONN = { name: "", endpoint_url: "", access_key_id: "", secret_access_key: "", bucket: "", verify_tls: false };

function statusDot(status) {
  if (status === "ok") return <span className="dot dot-ok" />;
  if (status === "error") return <span className="dot dot-err" />;
  return <span className="dot dot-unknown" />;
}

function S3Tab({ role }) {
  const [rows, setRows] = useState([]);
  const [editing, setEditing] = useState(null); // null=hidden, {}=new, {...}=edit
  const [form, setForm] = useState(EMPTY_CONN);
  const [busy, setBusy] = useState(false);
  const [testMsg, setTestMsg] = useState(null);
  const [bucketOptions, setBucketOptions] = useState([]);

  async function refresh() {
    try { const d = await getS3Connections(role); setRows(d.connections || []); }
    catch (e) { toastErr(e.message || e); }
  }
  useEffect(() => { refresh(); setEditing(null); }, [role]);

  function openNew() { setForm(EMPTY_CONN); setEditing({}); setTestMsg(null); setBucketOptions([]); }
  function openEdit(c) {
    setForm({ name: c.name, endpoint_url: c.endpoint_url, access_key_id: c.access_key_id,
             secret_access_key: "", bucket: c.bucket, verify_tls: c.verify_tls });
    setEditing(c); setTestMsg(null);
    setBucketOptions(c.bucket ? [c.bucket] : []); // hiện sẵn bucket đã lưu, khỏi cần test lại mới thấy
  }

  // Đổi endpoint/access key/secret → danh sách bucket cũ không còn đáng tin, phải test lại.
  function updateConnField(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
    setBucketOptions([]);
    setTestMsg(null);
  }

  async function testDraft() {
    setBusy(true); setTestMsg(null);
    try {
      const { bucket: _bucket, ...rest } = form; // luôn test kiểu "liệt kê bucket", không test 1 bucket cụ thể
      const body = editing?.id
        ? { id: editing.id, use_stored_secret: !form.secret_access_key, role, ...rest, bucket: "" }
        : { role, ...rest, bucket: "" };
      const res = await testS3ConnectionDraft(body);
      setTestMsg(res);
      if (res.result === "ok" && res.buckets) {
        setBucketOptions(res.buckets);
        setForm((f) => (res.buckets.includes(f.bucket) ? f : { ...f, bucket: "" }));
      }
    } catch (e) { setTestMsg({ result: "error", message: e.message || String(e) }); }
    finally { setBusy(false); }
  }

  async function save() {
    if (!form.name || !form.endpoint_url || !form.access_key_id || !form.bucket) {
      toastErr("Điền đủ tên/endpoint/access key/bucket"); return;
    }
    if (!editing?.id && !form.secret_access_key) { toastErr("Cần secret khi tạo mới"); return; }
    setBusy(true);
    try {
      if (editing?.id) await updateS3Connection(editing.id, form);
      else await createS3Connection({ role, ...form });
      toastOk("Đã lưu connection");
      setEditing(null); refresh();
    } catch (e) { toastErr(e.message || e); } finally { setBusy(false); }
  }

  async function test(id) {
    try { const res = await testS3Connection(id); toastOk(`${res.result === "ok" ? "OK" : "Lỗi"}: ${res.message}`); refresh(); }
    catch (e) { toastErr(e.message || e); }
  }

  async function remove(c) {
    if (!window.confirm(`Xóa connection "${c.name}"?`)) return;
    try {
      await deleteS3Connection(c.id);
      toastOk("Đã xóa"); refresh();
    } catch (e) {
      const detail = e.detail;
      if (detail && detail.referenced_gcns) {
        if (window.confirm(`Còn ${detail.referenced_gcns} GCN tham chiếu connection này. Vẫn xóa (force)?`)) {
          try { await deleteS3Connection(c.id, true); toastOk("Đã xóa (force)"); refresh(); }
          catch (e2) { toastErr(e2.message || e2); }
        }
      } else toastErr(e.message || e);
    }
  }

  const isDest = role === "destination";
  const destLimitReached = isDest && rows.length >= 1;

  return (
    <div className="admin-tab-body">
      {isDest && !rows.length && (
        <div className="admin-warn">
          <Icon name="alertTriangle" size={16} />
          <div>
            <b>Chưa cấu hình S3 đích</b>
            <p>Upload tài liệu, cắt trang, export và upload logo sẽ KHÔNG hoạt động cho tới khi thêm 1 cấu hình đích.</p>
          </div>
        </div>
      )}
      <div className="admin-tab-toolbar">
        <span className="muted small">
          {rows.length} connection
          {destLimitReached && " · chỉ hỗ trợ 1 cấu hình S3 đích, xoá cấu hình hiện tại trước khi đổi sang kho khác"}
        </span>
        {!destLimitReached && (
          <button className="primary sm" onClick={openNew}><Icon name="plus" size={13} /> Thêm</button>
        )}
      </div>

      <div className="tbl-dense s3-tbl">
        <div className="file-row s3-head">
          <span>Tên</span><span>Endpoint</span><span>Bucket</span><span>Trạng thái</span><span />
        </div>
        {rows.map((c) => (
          <div className="file-row s3-row" key={c.id}>
            <span className="fr-name">{c.name}</span>
            <span className="fr-meta">{c.endpoint_url}</span>
            <span className="fr-meta">{c.bucket}</span>
            <span className="fr-meta">
              {statusDot(c.last_check_status)}{" "}
              {c.last_check_status ? c.last_check_message : "Chưa test"}
            </span>
            <span className="s3-actions">
              <button className="ghost xs" title={`ID: ${c.id}`}
                onClick={() => copyToClipboard(c.id).then(() => toastOk("Đã copy ID connection")).catch(() => toastErr("Trình duyệt không hỗ trợ copy"))}>
                <Icon name="copy" size={13} />
              </button>
              <button className="ghost xs" onClick={() => test(c.id)}>Test</button>
              <button className="ghost xs" onClick={() => openEdit(c)}>Sửa</button>
              <button className="ghost xs danger" onClick={() => remove(c)}>Xóa</button>
            </span>
          </div>
        ))}
        {!rows.length && <div className="muted center" style={{ padding: 16 }}>Chưa có connection nào.</div>}
      </div>

      {editing && (
        <div className="s3-form">
          <h4>{editing.id ? `Sửa: ${editing.name}` : `Thêm ${role === "source" ? "S3 nguồn" : "S3 đích"}`}</h4>
          <div className="row">
            <input className="text-input" placeholder="Tên" value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <input className="text-input" placeholder="Endpoint URL" value={form.endpoint_url}
              onChange={(e) => updateConnField("endpoint_url", e.target.value)} />
          </div>
          <div className="row">
            <input className="text-input" placeholder="Access key" value={form.access_key_id}
              onChange={(e) => updateConnField("access_key_id", e.target.value)} />
            <input className="text-input" type="password"
              placeholder={editing.id ? "Secret (để trống = giữ nguyên)" : "Secret key"}
              value={form.secret_access_key}
              onChange={(e) => updateConnField("secret_access_key", e.target.value)} />
          </div>
          <div className="row">
            <select className="text-input" value={form.bucket} disabled={!bucketOptions.length}
              onChange={(e) => setForm({ ...form, bucket: e.target.value })}>
              <option value="">{bucketOptions.length ? "— Chọn bucket —" : "Test kết nối để chọn bucket"}</option>
              {bucketOptions.map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
            <label className="row" style={{ alignItems: "center" }}>
              <input type="checkbox" checked={form.verify_tls}
                onChange={(e) => setForm({ ...form, verify_tls: e.target.checked })} />
              Verify TLS (bật cho nguồn qua internet)
            </label>
          </div>
          {testMsg && (
            <div className={`fit-status ${testMsg.result === "ok" ? "ok" : "info"}`}>
              {statusDot(testMsg.result)} {testMsg.message}
            </div>
          )}
          <div className="admin-tab-foot">
            <button className="ghost sm" disabled={busy} onClick={testDraft}>Test kết nối</button>
            <button className="primary sm" disabled={busy} onClick={save}>Lưu</button>
            <button className="ghost sm" onClick={() => setEditing(null)}>Hủy</button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Tab: Dead-letter/lỗi (§Quy mô cực lớn 6) — retry hàng loạt ──────────────
// "Tất cả đợt" (batchId="") gộp mọi lô; lọc theo khoảng THỜI ĐIỂM LỖI (finished_at)
// để nhắm đúng đợt hỏng do sự cố hạ tầng (vd VLM khởi động lại).
const ALL_BATCHES = "__all__";

// datetime-local (giờ máy) → ISO UTC cho backend; rỗng ⇒ undefined.
function toIso(local) {
  if (!local) return undefined;
  const d = new Date(local);
  return isNaN(d) ? undefined : d.toISOString();
}

function ErrorsTab() {
  const [rows, setRows] = useState([]);
  const [batchId, setBatchId] = useState(ALL_BATCHES);
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [busy, setBusy] = useState(false);

  async function refresh() {
    try { const d = await listBatches(200); setRows(d.batches || []); }
    catch (e) { toastErr(e.message || e); }
  }
  useEffect(() => { refresh(); }, []);

  const isAll = batchId === ALL_BATCHES;
  const current = rows.find((b) => b.batch_id === batchId);
  const counts = current?.counts || {};
  // Tất cả đợt: cộng dồn error/dead qua mọi lô để hiển thị (retry thực tế do
  // backend đếm lại theo finished_at, con số này chỉ là ước lượng trần).
  const totalError = rows.reduce((s, b) => s + (b.counts?.error || 0), 0);
  const totalDead = rows.reduce((s, b) => s + (b.counts?.dead || 0), 0);
  const totalProcessing = rows.reduce((s, b) => s + (b.counts?.processing || 0), 0);
  const errorCount = isAll ? totalError : (counts.error || 0);
  const deadCount = isAll ? totalDead : (counts.dead || 0);
  const processingCount = isAll ? totalProcessing : (counts.processing || 0);
  const hasTimeFilter = !!(since || until);

  async function retry(errorKind) {
    setBusy(true);
    try {
      const res = await retryErrors({
        batchId: isAll ? undefined : batchId,
        errorKind,
        since: toIso(since),
        until: toIso(until),
      });
      const scope = res.batches != null ? ` (${res.batches} lô)` : "";
      toastOk(`Đã đưa lại vào hàng chờ ${res.requeued} hồ sơ${scope}`);
      refresh();
    } catch (e) { toastErr(e.message || e); } finally { setBusy(false); }
  }

  async function release() {
    setBusy(true);
    try {
      const res = await releaseStuck({ batchId: isAll ? undefined : batchId });
      const scope = res.batches != null ? ` (${res.batches} lô)` : "";
      toastOk(`Đã giải phóng ${res.released} hồ sơ kẹt${scope}`);
      refresh();
    } catch (e) { toastErr(e.message || e); } finally { setBusy(false); }
  }

  return (
    <div className="admin-tab-body">
      <label className="field-label" htmlFor="errors-batch">Phạm vi</label>
      <select id="errors-batch" className="text-input" value={batchId}
        onChange={(e) => setBatchId(e.target.value)}>
        <option value={ALL_BATCHES}>— Tất cả đợt —</option>
        {rows.map((b) => (
          <option key={b.batch_id} value={b.batch_id}>{b.name} · {b.file_count} hồ sơ</option>
        ))}
      </select>

      <label className="field-label" style={{ marginTop: 10 }}>Lọc theo thời điểm lỗi (tùy chọn)</label>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input type="datetime-local" className="text-input" style={{ flex: 1, minWidth: 180 }}
          value={since} onChange={(e) => setSince(e.target.value)} aria-label="Từ" />
        <input type="datetime-local" className="text-input" style={{ flex: 1, minWidth: 180 }}
          value={until} onChange={(e) => setUntil(e.target.value)} aria-label="Đến" />
      </div>
      {(since || until) && (
        <button className="ghost sm" style={{ marginTop: 6 }}
          onClick={() => { setSince(""); setUntil(""); }}>Xóa lọc thời gian</button>
      )}

      <p className="muted small" style={{ marginTop: 10 }}>
        Lỗi: <b>{errorCount}</b> · Poison/dead: <b>{deadCount}</b>
        {isAll && " · gộp mọi đợt"}
        {hasTimeFilter && " · con số trên chưa trừ lọc thời gian — backend đếm lại khi chạy"}
      </p>
      <div className="admin-tab-foot">
        <button className="primary sm" disabled={busy || (!errorCount && !hasTimeFilter)}
          onClick={() => retry("transient")}>
          Retry lỗi tạm thời
        </button>
        <button className="ghost sm" disabled={busy || (!errorCount && !hasTimeFilter)}
          onClick={() => retry(undefined)}>
          Retry mọi lỗi
        </button>
      </div>
      {!!deadCount && (
        <p className="muted small">
          {deadCount} hồ sơ "dead" (nghi làm worker treo — poison doc) KHÔNG nằm trong phạm vi retry
          hàng loạt, cần soi thủ công.
        </p>
      )}

      <hr style={{ margin: "16px 0", border: "none", borderTop: "1px solid var(--border, #e2e2e2)" }} />

      <label className="field-label">Giải phóng job kẹt</label>
      <p className="muted small" style={{ marginTop: 4 }}>
        Sau khi tắt/bật hay build lại worker, một số hồ sơ có thể mắc kẹt ở{" "}
        <b>đang xử lý</b> mà không worker nào chạy. Nút này đưa chúng về hàng chờ ngay
        (thay vì chờ worker tự nhặt lại sau ~30′). Chỉ đụng hồ sơ kẹt trên 10 phút để
        KHÔNG giật hồ sơ đang extract dở (extract 1 hồ sơ có thể mất vài phút).
      </p>
      <p className="muted small">Đang xử lý: <b>{processingCount}</b>{isAll && " · gộp mọi đợt"}</p>
      <div className="admin-tab-foot">
        <button className="ghost sm" disabled={busy || !processingCount} onClick={release}>
          Giải phóng job kẹt
        </button>
      </div>
    </div>
  );
}

