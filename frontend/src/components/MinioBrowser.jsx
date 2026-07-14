import React, { useEffect, useMemo, useRef, useState } from "react";
import Icon from "./Icon.jsx";
import { browseMinio, browseFolderProgress, importFromMinio } from "../api.js";
import { toastErr, toastOk } from "../toast.js";
import { STATUS_LABEL } from "./ExtractTable.jsx";

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

// Hàng đợi giới hạn số request tiến độ thư mục chạy đồng thời — tránh dội một
// loạt request nặng lên server khi 1 trang hiện nhiều thư mục cùng lúc. Chậm
// hơn (xử lý tuần tự theo lô) nhưng KHÔNG bỏ sót — mọi thư mục đều được tính,
// chỉ là tới lượt. Đặt ở module scope: áp dụng chung dù có nhiều MinioBrowser.
const MAX_CONCURRENT_PROGRESS = 10;
let runningProgress = 0;
const progressQueue = [];
function scheduleProgress(task) {
  progressQueue.push(task);
  pumpProgressQueue();
}
function pumpProgressQueue() {
  while (runningProgress < MAX_CONCURRENT_PROGRESS && progressQueue.length) {
    const task = progressQueue.shift();
    runningProgress++;
    task().finally(() => { runningProgress--; pumpProgressQueue(); });
  }
}

// Duyệt 1 cấp kho MinIO nguồn + chọn file lẻ (đồng bộ) hoặc nhập cả thư mục
// (nền, qua worker-queue) — dense list theo PLAN_.md §Chuẩn UI dense.
export default function MinioBrowser({ sourceId, batchId, onImported }) {
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
  const [progress, setProgress] = useState({}); // "sourceId:prefix" → {total,imported,capped} | null (lỗi)
  const requestedRef = useRef(new Set()); // tránh gọi lại tiến độ 1 thư mục nhiều lần
  const aliveRef = useRef(true); // né setState sau khi unmount (task còn xếp hàng/đang chạy)
  useEffect(() => () => { aliveRef.current = false; }, []);

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

  // Tiến độ import (x/y) từng thư mục con — lười tải theo trang đang xem, xếp
  // hàng giới hạn đồng thời (scheduleProgress) thay vì bắn hết cùng lúc. Cache
  // server-side theo source+prefix (dùng chung mọi phiên/tài khoản) nên quay
  // lại thư mục đã xem hoặc người khác xem không phải đếm lại từ đầu.
  useEffect(() => {
    folders.forEach((f) => {
      const k = `${sourceId}:${f}`;
      if (requestedRef.current.has(k)) return;
      requestedRef.current.add(k);
      scheduleProgress(() => browseFolderProgress(sourceId, f)
        .then((d) => { if (aliveRef.current) setProgress((prev) => ({ ...prev, [k]: d })); })
        .catch(() => { if (aliveRef.current) setProgress((prev) => ({ ...prev, [k]: null })); }));
    });
  }, [folders, sourceId]);

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
        keys: Array.from(selected), batch_id: batchId || undefined,
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
        prefix, recursive: true, batch_id: batchId || undefined,
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

        {!loading && !error && visFolders.map((f) => {
          const prog = progress[`${sourceId}:${f}`];
          const done = prog && prog.total > 0 && prog.imported >= prog.total;
          return (
            <div key={f} className="file-row is-folder" role="button" tabIndex={0}
              onClick={() => setPrefix(f)} onKeyDown={(e) => { if (e.key === "Enter") setPrefix(f); }}>
              <span className="fr-check" />
              <Icon name="folder" size={16} className="fr-ico folder" />
              <span className="fr-name">{folderName(f)}</span>
              {prog === undefined && <span className="mb-progress mb-progress-loading">đang đếm…</span>}
              {prog && (
                <span className={`mb-progress ${done ? "done" : ""}`}
                  title="Số file trong thư mục đã từng import / tổng số file">
                  {done && <Icon name="checkCircle" size={12} />}
                  {prog.imported}{prog.capped ? "+" : ""}/{prog.total}{prog.capped ? "+" : ""} đã nhập
                </span>
              )}
            </div>
          );
        })}

        {!loading && !error && visFiles.map((f) => (
          <label key={f.key} className={`file-row ${selected.has(f.key) ? "sel" : ""}`}>
            <input type="checkbox" className="fr-check" checked={selected.has(f.key)}
              onChange={() => toggle(f.key)} />
            <span className="fr-chip">PDF</span>
            <span className="fr-name" title={f.name}>{f.name}</span>
            {f.imported && (
              <span className="mb-imported" title={f.imported_info
                ? `Đã nhập trước đó · ${fmtDate(f.imported_info.created_at) || "?"} · ${STATUS_LABEL[f.imported_info.status] || f.imported_info.status || "?"}`
                : "Đã từng import vào hệ thống"}>
                <Icon name="checkCircle" size={12} /> Đã nhập
              </span>
            )}
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
