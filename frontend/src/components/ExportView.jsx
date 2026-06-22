import React, { useEffect, useState } from "react";
import Icon from "./Icon.jsx";
import { listRows, listBatches, downloadCsv } from "../api.js";
import { subscribeEvents } from "../events.js";
import { toastOk, toastErr } from "../toast.js";

const REVIEW = { "": "Mọi hậu kiểm", reviewed: "Đã duyệt", needs_review: "Cần xem", unreviewed: "Chưa kiểm" };

// Khung nhìn dạng HÀNG phẳng (đã áp hậu kiểm) — xem toàn bộ + tải CSV cho FME/Excel.
export default function ExportView() {
  const [batches, setBatches] = useState([]);
  const [batchId, setBatchId] = useState("");
  const [review, setReview] = useState("reviewed");
  const [cols, setCols] = useState([]);
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);

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
              <tr key={i}>{cols.map((c) => <td key={c} title={r[c] == null ? "" : String(r[c])}>{r[c] == null ? "" : String(r[c])}</td>)}</tr>
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
  );
}
