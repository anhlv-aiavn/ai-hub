import React, { useEffect, useState } from "react";
import Icon from "./Icon.jsx";
import GcnPdf from "./GcnPdf.jsx";
import { listRows, listBatches, downloadCsv, getStats } from "../api.js";
import { subscribeEvents } from "../events.js";
import { toastOk, toastErr } from "../toast.js";

const REVIEW = { "": "Mọi hậu kiểm", reviewed: "Đã duyệt", needs_review: "Cần xem", unreviewed: "Chưa kiểm" };
const FILE_COLS = new Set(["Tệp gốc", "Tệp cắt"]);

const STATUS_SEGS = [
  { key: "done", label: "Xong", cls: "ok" },
  { key: "processing", label: "Đang xử lý", cls: "info" },
  { key: "queued", label: "Chờ", cls: "muted" },
  { key: "error", label: "Lỗi", cls: "danger" },
  { key: "skip", label: "Bỏ qua", cls: "warn" },
];
const REVIEW_SEGS = [
  { key: "reviewed", label: "Đã duyệt", cls: "ok" },
  { key: "needs_review", label: "Cần xem", cls: "warn" },
  { key: "unreviewed", label: "Chưa kiểm", cls: "muted" },
];

const fmt = (n) => (n || 0).toLocaleString("vi-VN");

function Kpi({ icon, label, value, sub }) {
  return (
    <div className="kpi">
      <div className="kpi-ico"><Icon name={icon} size={18} /></div>
      <div className="kpi-body">
        <div className="kpi-val">{value}</div>
        <div className="kpi-label">{label}</div>
        {sub != null && <div className="kpi-sub">{sub}</div>}
      </div>
    </div>
  );
}

function SegBar({ title, segs, data }) {
  const items = segs.map((x) => ({ ...x, n: data[x.key] || 0 }));
  const total = items.reduce((a, b) => a + b.n, 0) || 1;
  return (
    <div className="seg-block">
      <div className="seg-title">{title}</div>
      <div className="seg-bar" role="img"
        aria-label={items.map((x) => `${x.label}: ${x.n}`).join(", ")}>
        {items.filter((x) => x.n > 0).map((x) => (
          <div key={x.key} className={`seg seg-${x.cls}`} style={{ width: `${(x.n / total) * 100}%` }}
            title={`${x.label}: ${x.n}`} />
        ))}
      </div>
      <div className="seg-legend">
        {items.map((x) => (
          <span key={x.key} className="seg-leg">
            <i className={`seg-dot seg-${x.cls}`} /> {x.label} <b>{fmt(x.n)}</b>
          </span>
        ))}
      </div>
    </div>
  );
}

// Thống kê: KPI + breakdown trạng thái/hậu kiểm + cảnh báo, kèm xuất CSV/bảng phẳng (FME).
export default function ExportView({ onAlert }) {
  const [batches, setBatches] = useState([]);
  const [batchId, setBatchId] = useState("");
  const [review, setReview] = useState("");
  const [stats, setStats] = useState(null);
  const [cols, setCols] = useState([]);
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState(null); // {gcnId, cutIndex, title}
  const [pvPage, setPvPage] = useState(1);

  function openPreview(p) { setPvPage(1); setPreview(p); }

  async function refresh() {
    setLoading(true);
    try {
      const [d, st] = await Promise.all([
        listRows({ batchId: batchId || undefined, review: review || undefined }),
        getStats({ batchId: batchId || undefined }),
      ]);
      setCols(d.columns || []);
      setRows(d.rows || []);
      setStats(st);
    } catch (e) { toastErr(e.message || e); } finally { setLoading(false); }
  }

  useEffect(() => { listBatches().then((d) => setBatches(d.batches || [])).catch(() => {}); }, []);
  useEffect(() => { refresh(); /* eslint-disable-next-line */ }, [batchId, review]);
  useEffect(() => {
    let t = null;
    const un = subscribeEvents(() => { clearTimeout(t); t = setTimeout(refresh, 800); });
    return () => { un(); clearTimeout(t); };
    // eslint-disable-next-line
  }, [batchId, review]);

  async function csv() {
    try { await downloadCsv({ batchId: batchId || undefined, review: review || undefined }); toastOk("Đã tải CSV"); }
    catch (e) { toastErr(e.message || e); }
  }

  function cell(col, r) {
    if (col === "Tệp gốc") {
      if (!r._gcn_id) return "—";
      return (
        <button className="link-btn" onClick={() => openPreview({ gcnId: r._gcn_id, cutIndex: null, title: r["Tệp gốc"] })}>
          <Icon name="fileText" size={13} /> {r["Tệp gốc"] || "Xem"}
        </button>
      );
    }
    if (col === "Tệp cắt") {
      if (r._cut_index === null || r._cut_index === undefined) return <span className="muted">—</span>;
      return (
        <button className="link-btn" onClick={() => openPreview({ gcnId: r._gcn_id, cutIndex: r._cut_index, title: r["Tệp cắt"] })}>
          <Icon name="scissors" size={13} /> {r["Tệp cắt"] || "Xem cắt"}
        </button>
      );
    }
    const v = r[col];
    return v == null ? "" : String(v);
  }

  const s = stats || {};
  const st = s.by_status || {};
  const done = st.done || 0;
  const completion = s.files ? Math.round((done / s.files) * 100) : 0;
  const alerts = [
    { key: "error", label: "Tệp lỗi", n: st.error || 0, cls: "danger", icon: "alertTriangle", status: "error" },
    { key: "skip", label: "Bỏ qua (quá lớn…)", n: st.skip || 0, cls: "warn", icon: "ban", status: "skip" },
    { key: "missing", label: "Thiếu Số phát hành", n: s.missing_sph || 0, cls: "warn", icon: "alertTriangle", status: "done" },
    { key: "unrev", label: "Xong, chưa hậu kiểm", n: s.unreviewed_done || 0, cls: "info", icon: "clock", status: "done" },
  ].filter((a) => a.n > 0);

  return (
    <div className="panel export-view">
      <div className="et-toolbar">
        <h2>Thống kê</h2>
        <div className="et-filters">
          <select value={batchId} onChange={(e) => setBatchId(e.target.value)}>
            <option value="">Tất cả lô</option>
            {batches.map((b) => <option key={b.batch_id} value={b.batch_id}>{b.name} · {b.file_count} giấy</option>)}
          </select>
          <button className="ghost sm" onClick={refresh}><Icon name="refresh" size={14} /> Làm mới</button>
        </div>
      </div>

      <div className="dash">
        <div className="kpi-row">
          <Kpi icon="layers" label="Tổng tệp" value={fmt(s.files)} />
          <Kpi icon="fileText" label="GCN bóc ra" value={fmt(s.gcns)} />
          <Kpi icon="scissors" label="Tổng trang" value={fmt(s.pages)} />
          <Kpi icon="checkCircle" label="Hoàn tất" value={`${completion}%`} sub={`${fmt(done)}/${fmt(s.files)} tệp`} />
        </div>

        {alerts.length > 0 && (
          <div className="alert-row">
            {alerts.map((a) => (
              <button key={a.key} className={`alert-tile al-${a.cls}`}
                onClick={() => onAlert?.(a.status)} title="Mở Kết quả trích xuất theo trạng thái này">
                <Icon name={a.icon} size={16} />
                <span className="al-n">{fmt(a.n)}</span>
                <span className="al-label">{a.label}</span>
              </button>
            ))}
          </div>
        )}

        <div className="seg-row">
          <SegBar title="Trạng thái xử lý" segs={STATUS_SEGS} data={st} />
          <SegBar title="Hậu kiểm" segs={REVIEW_SEGS} data={s.by_review || {}} />
        </div>
      </div>

      <div className="export-sec">
        <div className="export-head">
          <h3>Xuất dữ liệu <span className="muted">(CSV / FME · 1 hàng/thửa)</span></h3>
          <div className="et-filters">
            <select value={review} onChange={(e) => setReview(e.target.value)}>
              {Object.entries(REVIEW).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <span className="muted ev-count">{rows.length} hàng</span>
            <button className="primary sm" onClick={csv}><Icon name="download" size={14} /> Tải CSV</button>
          </div>
        </div>

        <div className="et-scroll">
          <table className="et-grid flat-grid">
            <thead>
              <tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>{cols.map((c) => (
                  <td key={c} title={FILE_COLS.has(c) ? "" : (r[c] == null ? "" : String(r[c]))}>{cell(c, r)}</td>
                ))}</tr>
              ))}
              {!rows.length && (
                <tr><td colSpan={cols.length || 1} className="muted center">
                  {loading ? "Đang tải…" : "Chưa có hàng nào khớp bộ lọc."}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {preview && (
        <div className="modal-overlay" onClick={() => setPreview(null)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <b>{preview.cutIndex != null ? "File cắt" : "File gốc"} · {preview.title}</b>
              <button className="icon-btn" onClick={() => setPreview(null)} aria-label="Đóng"><Icon name="x" size={16} /></button>
            </div>
            <div className="modal-body">
              <GcnPdf gcnId={preview.gcnId} cutIndex={preview.cutIndex} page={pvPage} onPageChange={setPvPage} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
