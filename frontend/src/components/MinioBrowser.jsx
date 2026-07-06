import React, { useEffect, useMemo, useState } from "react";
import Icon from "./Icon.jsx";
import { browseMinio, importFromMinio } from "../api.js";
import { toastErr, toastOk } from "../toast.js";

function fmtSize(n) {
  if (!n && n !== 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
function fmtDate(v) {
  if (!v) return "";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric" });
}

// Duyệt 1 cấp kho MinIO nguồn + chọn file lẻ (đồng bộ) hoặc nhập cả thư mục
// (nền, qua worker-queue) — dense list theo PLAN_.md §Chuẩn UI dense.
export default function MinioBrowser({ sourceId, branch, batchId, onImported }) {
  const [prefix, setPrefix] = useState("");
  const [folders, setFolders] = useState([]);
  const [files, setFiles] = useState([]);
  const [nextToken, setNextToken] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState(() => new Set());
  const [busy, setBusy] = useState(false);

  async function load(p, token) {
    if (!token) { setLoading(true); setError(""); } else { setLoadingMore(true); }
    try {
      const d = await browseMinio(sourceId, p, token);
      setFolders((prev) => (token ? dedupe([...prev, ...(d.folders || [])]) : (d.folders || [])));
      setFiles((prev) => (token ? [...prev, ...(d.files || [])] : (d.files || [])));
      setNextToken(d.next_token || null);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setLoading(false); setLoadingMore(false);
    }
  }
  function dedupe(arr) { return Array.from(new Set(arr)); }

  useEffect(() => {
    setSelected(new Set()); setFilter("");
    load(prefix, null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceId, prefix]);

  const crumbs = useMemo(() => {
    const parts = prefix.split("/").filter(Boolean);
    const out = [{ label: "Kho gốc", value: "" }];
    let acc = "";
    for (const p of parts) { acc += `${p}/`; out.push({ label: p, value: acc }); }
    return out;
  }, [prefix]);

  const q = filter.trim().toLowerCase();
  const visFolders = q ? folders.filter((f) => folderName(f).toLowerCase().includes(q)) : folders;
  const visFiles = q ? files.filter((f) => f.name.toLowerCase().includes(q)) : files;

  function folderName(f) {
    const parts = f.split("/").filter(Boolean);
    return parts[parts.length - 1] || f;
  }

  function toggle(key) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  async function importSelected() {
    if (!selected.size || busy) return;
    setBusy(true);
    try {
      const res = await importFromMinio(sourceId, {
        keys: Array.from(selected), branch, batch_id: batchId || undefined,
      });
      toastOk(`Đã nhập ${res.created} tệp${res.skipped ? ` · bỏ qua ${res.skipped} (đã có)` : ""}`);
      setSelected(new Set());
      onImported?.(res.batch_id, { mode: "sync", created: res.created, skipped: res.skipped });
    } catch (e) { toastErr(e.message || e); } finally { setBusy(false); }
  }

  async function importFolder() {
    if (busy) return;
    const label = prefix || "toàn bộ kho gốc";
    if (!window.confirm(`Nhập TOÀN BỘ thư mục "${label}" (có thể rất nhiều tệp, chạy nền)?`)) return;
    setBusy(true);
    try {
      const res = await importFromMinio(sourceId, {
        prefix, recursive: true, branch, batch_id: batchId || undefined,
      });
      toastOk("Đang nhập thư mục ở nền — theo dõi tiến độ trong Kết quả trích xuất");
      onImported?.(res.batch_id, { mode: "async", status: res.status });
    } catch (e) { toastErr(e.message || e); } finally { setBusy(false); }
  }

  const total = visFolders.length + visFiles.length;

  return (
    <div className="minio-browser">
      <div className="mb-breadcrumb">
        {crumbs.map((c, i) => (
          <React.Fragment key={c.value}>
            {i > 0 && <Icon name="chevronRight" size={13} className="mb-sep" />}
            <button type="button" className={`mb-crumb ${c.value === prefix ? "cur" : ""}`}
              disabled={c.value === prefix} onClick={() => setPrefix(c.value)}>{c.label}</button>
          </React.Fragment>
        ))}
      </div>

      <div className="mb-header">
        <span className="mb-count">{total}</span>
        <span className="mb-title">{prefix ? folderName(prefix) : "Kho gốc"}</span>
        <button type="button" className="ghost xs" disabled={busy} onClick={importFolder}>
          <Icon name="upload" size={13} /> Nhập cả thư mục này
        </button>
      </div>

      <div className="mb-filter">
        <Icon name="search" size={14} />
        <input type="text" placeholder="Lọc theo tên (trong trang đã tải)…" value={filter}
          onChange={(e) => setFilter(e.target.value)} />
      </div>

      <div className="mb-list tbl-dense">
        {loading && Array.from({ length: 6 }).map((_, i) => <div key={i} className="file-row skeleton" />)}

        {!loading && !error && total === 0 && (
          <div className="mb-empty">
            <Icon name="folder" size={30} />
            <p>Thư mục này trống.</p>
          </div>
        )}

        {!loading && error && (
          <div className="mb-empty mb-error">
            <Icon name="alertTriangle" size={30} />
            <p>{error}</p>
            <button type="button" className="ghost sm" onClick={() => load(prefix, null)}>Thử lại</button>
          </div>
        )}

        {!loading && !error && visFolders.map((f) => (
          <div key={f} className="file-row is-folder" role="button" tabIndex={0}
            onClick={() => setPrefix(f)} onKeyDown={(e) => { if (e.key === "Enter") setPrefix(f); }}>
            <span className="fr-check" />
            <Icon name="folder" size={16} className="fr-ico folder" />
            <span className="fr-name">{folderName(f)}</span>
            <span className="fr-meta" />
          </div>
        ))}

        {!loading && !error && visFiles.map((f) => (
          <label key={f.key} className={`file-row ${selected.has(f.key) ? "sel" : ""}`}>
            <input type="checkbox" className="fr-check" checked={selected.has(f.key)}
              onChange={() => toggle(f.key)} />
            <span className="fr-chip">PDF</span>
            <span className="fr-name" title={f.name}>{f.name}</span>
            <span className="fr-meta">{fmtSize(f.size)} · {fmtDate(f.last_modified)}</span>
          </label>
        ))}

        {!loading && !error && nextToken && (
          <button type="button" className="mb-more" disabled={loadingMore}
            onClick={() => load(prefix, nextToken)}>
            {loadingMore ? "Đang tải…" : "Tải thêm"}
          </button>
        )}
      </div>

      {selected.size > 0 && (
        <div className="bulk-bar">
          <span>Đã chọn {selected.size} tệp</span>
          <button type="button" className="primary sm" disabled={busy} onClick={importSelected}>
            <Icon name="check" size={14} /> Nhập vào lô
          </button>
          <button type="button" className="ghost sm" disabled={busy} onClick={() => setSelected(new Set())}>
            Bỏ chọn
          </button>
        </div>
      )}
    </div>
  );
}
