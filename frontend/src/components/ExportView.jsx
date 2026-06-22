import React, { useEffect, useState } from "react";
import Icon from "./Icon.jsx";
import GcnPdf from "./GcnPdf.jsx";
import { listRows, listBatches, downloadCsv } from "../api.js";
import { subscribeEvents } from "../events.js";
import { toastOk, toastErr } from "../toast.js";

const REVIEW = { "": "Mọi hậu kiểm", reviewed: "Đã duyệt", needs_review: "Cần xem", unreviewed: "Chưa kiểm" };
const FILE_COLS = new Set(["Tệp gốc", "Tệp cắt"]);

// Khung nhìn dạng HÀNG phẳng (đã áp hậu kiểm) — xem toàn bộ + preview file gốc/cắt + tải CSV.
export default function ExportView() {
  const [batches, setBatches] = useState([]);
  const [batchId, setBatchId] = useState("");
  const [review, setReview] = useState("");
  const [cols, setCols] = useState([]);
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState(null); // {gcnId, cutIndex, title}
  const [pvPage, setPvPage] = useState(1);

  function openPreview(p) { setPvPage(1); setPreview(p); }

  async function refresh() {
    setLoading(true);
    try {
      const d = await listRows({ batchId: batchId || undefined, review: review || undefined });
      setCols(d.columns || []);
      setRows(d.rows || []);
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

  return (
    <div className="panel export-view">
      <div className="et-toolbar">
        <h2>Xuất dữ liệu</h2>
        <div className="et-filters">
          <select value={batchId} onChange={(e) => setBatchId(e.target.value)}>
            <option value="">Tất cả lô</option>
            {batches.map((b) => <option key={b.batch_id} value={b.batch_id}>{b.name} · {b.file_count} giấy</option>)}
          </select>
          <select value={review} onChange={(e) => setReview(e.target.value)}>
            {Object.entries(REVIEW).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <button className="ghost sm" onClick={refresh}><Icon name="refresh" size={14} /> Làm mới</button>
          <button className="primary sm" onClick={csv}><Icon name="download" size={14} /> Tải CSV</button>
        </div>
      </div>

      <div className="muted ev-count">{rows.length} hàng</div>

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
