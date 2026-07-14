import React, { useEffect, useState } from "react";
import Icon from "./Icon.jsx";
import Modal from "./Modal.jsx";
import {
  getSiteConfig, updateSiteConfig, uploadLogo, getSettingsStatus,
  getS3Connections, createS3Connection, updateS3Connection, deleteS3Connection,
  testS3Connection, testS3ConnectionDraft,
  listBatches, retryErrors, listUsers, getBatchUsers, assignBatchUser, unassignBatchUser,
} from "../api.js";
import { toastOk, toastErr } from "../toast.js";
import { copyToClipboard } from "../clipboard.js";

const TABS = [
  ["org", "Tổ chức"],
  ["batches", "Lô & phân quyền"],
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
        {tab === "batches" && <BatchAccessTab />}
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

// ── Tab: Lô & phân quyền — gán/bỏ gán user cho 1 lô cụ thể. Đối xứng với
// Users.jsx (gán lô cho 1 user): cả hai đều ghi vào `user.assigned_batch_ids`,
// nên thao tác từ bên này phản ánh ngay ở bên kia. ──────────────────────────
function BatchAccessTab() {
  const [batches, setBatches] = useState([]);
  const [batchId, setBatchId] = useState("");
  const [assigned, setAssigned] = useState([]); // [{username, role}]
  const [allUsers, setAllUsers] = useState([]);
  const [addUsername, setAddUsername] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    listBatches(500).then((d) => setBatches(d.batches || [])).catch(() => {});
    listUsers().then((d) => setAllUsers(d.users || [])).catch(() => {});
  }, []);

  async function refreshAssigned() {
    if (!batchId) { setAssigned([]); return; }
    try { setAssigned((await getBatchUsers(batchId)).users || []); }
    catch (e) { toastErr(e.message || e); }
  }
  useEffect(() => { refreshAssigned(); /* eslint-disable-next-line */ }, [batchId]);

  const assignable = allUsers.filter((u) => u.role !== "admin"
    && !assigned.some((a) => a.username === u.username));

  async function assign() {
    if (!addUsername) return;
    setBusy(true);
    try {
      await assignBatchUser(batchId, addUsername);
      setAddUsername("");
      toastOk("Đã gán");
      refreshAssigned();
    } catch (e) { toastErr(e.message || e); } finally { setBusy(false); }
  }
  async function unassign(username) {
    setBusy(true);
    try { await unassignBatchUser(batchId, username); toastOk("Đã bỏ gán"); refreshAssigned(); }
    catch (e) { toastErr(e.message || e); } finally { setBusy(false); }
  }

  return (
    <div className="admin-tab-body">
      <label className="field-label" htmlFor="ba-batch">Chọn lô</label>
      <select id="ba-batch" className="text-input" value={batchId} onChange={(e) => setBatchId(e.target.value)}>
        <option value="">— Chọn lô —</option>
        {batches.map((b) => (
          <option key={b.batch_id} value={b.batch_id}>{b.name} · {b.file_count} hồ sơ</option>
        ))}
      </select>

      {batchId && (
        <>
          <label className="field-label">Tài khoản được truy cập ({assigned.length})</label>
          <div className="taginput">
            {assigned.map((a) => (
              <span className="tag" key={a.username}>{a.username}
                <button type="button" disabled={busy} onClick={() => unassign(a.username)} aria-label={`Bỏ ${a.username}`}>
                  <Icon name="x" size={11} />
                </button>
              </span>
            ))}
            {!assigned.length && <span className="muted small">Chưa có ai được gán lô này.</span>}
          </div>

          <div className="admin-tab-toolbar">
            <select className="text-input" value={addUsername} onChange={(e) => setAddUsername(e.target.value)}>
              <option value="">— Chọn tài khoản để gán —</option>
              {assignable.map((u) => <option key={u.username} value={u.username}>{u.username} ({u.role})</option>)}
            </select>
            <button className="primary sm" disabled={busy || !addUsername} onClick={assign}>
              <Icon name="plus" size={13} /> Gán
            </button>
          </div>
        </>
      )}
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

// ── Tab: Dead-letter/lỗi (§Quy mô cực lớn 6) — retry hàng loạt theo lô ──────
function ErrorsTab() {
  const [rows, setRows] = useState([]);
  const [batchId, setBatchId] = useState("");
  const [busy, setBusy] = useState(false);

  async function refresh() {
    try { const d = await listBatches(200); setRows(d.batches || []); }
    catch (e) { toastErr(e.message || e); }
  }
  useEffect(() => { refresh(); }, []);

  const current = rows.find((b) => b.batch_id === batchId);
  const counts = current?.counts || {};

  async function retry(errorKind) {
    if (!batchId) return;
    setBusy(true);
    try {
      const res = await retryErrors({ batchId, errorKind });
      toastOk(`Đã đưa lại vào hàng chờ ${res.requeued} hồ sơ`);
      refresh();
    } catch (e) { toastErr(e.message || e); } finally { setBusy(false); }
  }

  return (
    <div className="admin-tab-body">
      <label className="field-label" htmlFor="errors-batch">Chọn lô</label>
      <select id="errors-batch" className="text-input" value={batchId}
        onChange={(e) => setBatchId(e.target.value)}>
        <option value="">— Chọn lô —</option>
        {rows.map((b) => (
          <option key={b.batch_id} value={b.batch_id}>{b.name} · {b.file_count} hồ sơ</option>
        ))}
      </select>

      {current && (
        <>
          <p className="muted small" style={{ marginTop: 10 }}>
            Lỗi: <b>{counts.error || 0}</b> · Poison/dead: <b>{counts.dead || 0}</b>
          </p>
          <div className="admin-tab-foot">
            <button className="primary sm" disabled={busy || !counts.error} onClick={() => retry("transient")}>
              Retry lỗi tạm thời
            </button>
            <button className="ghost sm" disabled={busy || !counts.error} onClick={() => retry(undefined)}>
              Retry mọi lỗi
            </button>
          </div>
          {!!counts.dead && (
            <p className="muted small">
              {counts.dead} hồ sơ "dead" (nghi làm worker treo — poison doc) KHÔNG nằm trong phạm vi retry
              hàng loạt, cần soi thủ công.
            </p>
          )}
        </>
      )}
    </div>
  );
}

