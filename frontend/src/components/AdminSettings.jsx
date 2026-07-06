import React, { useEffect, useState } from "react";
import Icon from "./Icon.jsx";
import {
  getSiteConfig, updateSiteConfig,
  getS3Connections, createS3Connection, updateS3Connection, deleteS3Connection,
  testS3Connection, testS3ConnectionDraft, getAuditLog,
} from "../api.js";
import { toastOk, toastErr } from "../toast.js";

const TABS = [
  ["org", "Tổ chức & chi nhánh"],
  ["source", "S3 nguồn"],
  ["dest", "S3 đích"],
  ["audit", "Audit log"],
];

// Modal cấu hình hệ thống (admin-only) — tabs. Mở từ AccountMenu, cạnh "Quản trị
// tài khoản". Pattern modal theo Users.jsx (panel + export-head + nút đóng).
export default function AdminSettings({ onClose }) {
  const [tab, setTab] = useState("org");
  return (
    <div className="panel admin-settings">
      <div className="export-head">
        <h3>Cấu hình hệ thống</h3>
        <button className="icon-btn" onClick={onClose} aria-label="Đóng"><Icon name="x" size={16} /></button>
      </div>
      <nav className="admin-tabs">
        {TABS.map(([k, label]) => (
          <button key={k} className={k === tab ? "tab active" : "tab"} onClick={() => setTab(k)}>{label}</button>
        ))}
      </nav>
      {tab === "org" && <OrgTab />}
      {tab === "source" && <S3Tab role="source" />}
      {tab === "dest" && <S3Tab role="destination" />}
      {tab === "audit" && <AuditTab />}
    </div>
  );
}

// ── Tab: Tổ chức & chi nhánh ─────────────────────────────────────────────────
function OrgTab() {
  const [cfg, setCfg] = useState(null);
  const [newBranch, setNewBranch] = useState("");
  const [busy, setBusy] = useState(false);
  const [pendingConfirm, setPendingConfirm] = useState(null); // {message, removed_branches, users, batches, gcns}

  async function refresh() {
    try { setCfg(await getSiteConfig()); } catch (e) { toastErr(e.message || e); }
  }
  useEffect(() => { refresh(); }, []);

  function setField(path, value) {
    setCfg((prev) => {
      const next = { ...prev };
      if (path[0] === "branding") next.branding = { ...next.branding, [path[1]]: value };
      else next[path[0]] = value;
      return next;
    });
  }

  function addBranch() {
    const v = newBranch.trim();
    if (!v || (cfg.branches || []).includes(v)) return;
    setCfg({ ...cfg, branches: [...(cfg.branches || []), v] });
    setNewBranch("");
  }
  function removeBranch(b) {
    setCfg({ ...cfg, branches: (cfg.branches || []).filter((x) => x !== b) });
  }

  async function save(confirm = false) {
    setBusy(true);
    try {
      await updateSiteConfig({ name: cfg.name, branches: cfg.branches, branding: cfg.branding }, confirm);
      toastOk("Đã lưu cấu hình tổ chức");
      setPendingConfirm(null);
      refresh();
    } catch (e) {
      const detail = e.detail || e.message;
      if (detail && typeof detail === "object" && detail.removed_branches) {
        setPendingConfirm(detail);
      } else {
        toastErr(e.message || e);
      }
    } finally { setBusy(false); }
  }

  if (!cfg) return <div className="muted" style={{ padding: 16 }}>Đang tải…</div>;

  return (
    <div className="admin-tab-body">
      <label className="field-label" htmlFor="org-name">Tên tổ chức</label>
      <input id="org-name" className="text-input" value={cfg.name || ""}
        onChange={(e) => setField(["name"], e.target.value)} />

      <label className="field-label" htmlFor="org-logo">Logo (URL)</label>
      <input id="org-logo" className="text-input" value={cfg.branding?.logo_url || ""}
        onChange={(e) => setField(["branding", "logo_url"], e.target.value)} />

      <label className="field-label" htmlFor="org-brand">Tên hiển thị (branding)</label>
      <input id="org-brand" className="text-input" value={cfg.branding?.org_name || ""}
        onChange={(e) => setField(["branding", "org_name"], e.target.value)} />

      <label className="field-label" htmlFor="org-copy">Copyright</label>
      <input id="org-copy" className="text-input" value={cfg.branding?.copyright_text || ""}
        onChange={(e) => setField(["branding", "copyright_text"], e.target.value)} />

      <label className="field-label">Chi nhánh ({(cfg.branches || []).length})</label>
      <div className="taginput">
        {(cfg.branches || []).map((b) => (
          <span className="tag" key={b}>{b}
            <button type="button" onClick={() => removeBranch(b)} aria-label={`Bỏ ${b}`}>
              <Icon name="x" size={11} />
            </button>
          </span>
        ))}
        <input value={newBranch} placeholder="Thêm chi nhánh…" onChange={(e) => setNewBranch(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addBranch(); } }} />
      </div>

      {pendingConfirm && (
        <div className="admin-warn">
          <Icon name="alertTriangle" size={16} />
          <div>
            <b>Chi nhánh sắp bớt vẫn còn được tham chiếu</b>
            <p>{(pendingConfirm.removed_branches || []).join(", ")} — {pendingConfirm.users} tài khoản,{" "}
              {pendingConfirm.batches} lô, {pendingConfirm.gcns} GCN đang dùng.</p>
            <button className="primary sm" disabled={busy} onClick={() => save(true)}>Vẫn lưu</button>
            <button className="ghost sm" onClick={() => setPendingConfirm(null)}>Hủy</button>
          </div>
        </div>
      )}

      <div className="admin-tab-foot">
        <button className="primary" disabled={busy} onClick={() => save(false)}>
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

  async function refresh() {
    try { const d = await getS3Connections(role); setRows(d.connections || []); }
    catch (e) { toastErr(e.message || e); }
  }
  useEffect(() => { refresh(); setEditing(null); }, [role]);

  function openNew() { setForm(EMPTY_CONN); setEditing({}); setTestMsg(null); }
  function openEdit(c) {
    setForm({ name: c.name, endpoint_url: c.endpoint_url, access_key_id: c.access_key_id,
             secret_access_key: "", bucket: c.bucket, verify_tls: c.verify_tls });
    setEditing(c); setTestMsg(null);
  }

  async function testDraft() {
    setBusy(true); setTestMsg(null);
    try {
      const body = editing?.id
        ? { id: editing.id, use_stored_secret: !form.secret_access_key, role, ...form }
        : { role, ...form };
      const res = await testS3ConnectionDraft(body);
      setTestMsg(res);
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

  return (
    <div className="admin-tab-body">
      <div className="admin-tab-toolbar">
        <span className="muted small">{rows.length} connection</span>
        <button className="primary sm" onClick={openNew}><Icon name="plus" size={13} /> Thêm</button>
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
              onChange={(e) => setForm({ ...form, endpoint_url: e.target.value })} />
          </div>
          <div className="row">
            <input className="text-input" placeholder="Access key" value={form.access_key_id}
              onChange={(e) => setForm({ ...form, access_key_id: e.target.value })} />
            <input className="text-input" type="password"
              placeholder={editing.id ? "Secret (để trống = giữ nguyên)" : "Secret key"}
              value={form.secret_access_key}
              onChange={(e) => setForm({ ...form, secret_access_key: e.target.value })} />
          </div>
          <div className="row">
            <input className="text-input" placeholder="Bucket" value={form.bucket}
              onChange={(e) => setForm({ ...form, bucket: e.target.value })} />
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

// ── Tab: Audit log ───────────────────────────────────────────────────────────
function AuditTab() {
  const [items, setItems] = useState([]);
  const [cursor, setCursor] = useState(null);
  const [action, setAction] = useState("");
  const [actor, setActor] = useState("");
  const [open, setOpen] = useState(null);
  const [loading, setLoading] = useState(false);

  async function load(before) {
    setLoading(true);
    try {
      const d = await getAuditLog({ action: action || undefined, actor: actor || undefined, beforeId: before });
      setItems((prev) => (before ? [...prev, ...d.items] : d.items));
      setCursor(d.next_cursor);
    } catch (e) { toastErr(e.message || e); } finally { setLoading(false); }
  }
  useEffect(() => { load(null); }, [action, actor]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="admin-tab-body">
      <div className="row" style={{ marginBottom: 10 }}>
        <select className="text-input" style={{ width: "auto" }} value={action} onChange={(e) => setAction(e.target.value)}>
          <option value="">Mọi hành động</option>
          <option value="site_config.update">site_config.update</option>
          <option value="s3_connection.create">s3_connection.create</option>
          <option value="s3_connection.update">s3_connection.update</option>
          <option value="s3_connection.delete">s3_connection.delete</option>
          <option value="s3_connection.test">s3_connection.test</option>
        </select>
        <input className="text-input" style={{ width: "auto", flex: "1 1 150px" }} placeholder="Lọc theo actor…"
          value={actor} onChange={(e) => setActor(e.target.value)} />
      </div>

      <div className="tbl-dense audit-tbl">
        {items.map((r) => (
          <div key={r.id} className="audit-item">
            <div className="file-row audit-row" role="button" tabIndex={0}
              onClick={() => setOpen(open === r.id ? null : r.id)}>
              <span className="fr-meta mono" title={r.at}>{fmtRelative(r.at)}</span>
              <span className="fr-name">{r.actor}</span>
              <span className="badge">{r.action}</span>
              <span className="fr-meta">{r.target}</span>
              <Icon name={open === r.id ? "chevronDown" : "chevronRight"} size={14} />
            </div>
            {open === r.id && (
              <pre className="audit-detail">{JSON.stringify(r.detail, null, 2)}</pre>
            )}
          </div>
        ))}
        {!items.length && !loading && <div className="muted center" style={{ padding: 16 }}>Chưa có bản ghi.</div>}
      </div>

      {cursor && (
        <button type="button" className="ghost sm" disabled={loading} onClick={() => load(cursor)}>
          {loading ? "Đang tải…" : "Tải thêm"}
        </button>
      )}
    </div>
  );
}

function fmtRelative(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const diffMin = Math.round((Date.now() - d.getTime()) / 60000);
  if (diffMin < 1) return "vừa xong";
  if (diffMin < 60) return `${diffMin} phút trước`;
  const diffH = Math.round(diffMin / 60);
  if (diffH < 24) return `${diffH} giờ trước`;
  return d.toLocaleDateString("vi-VN");
}
