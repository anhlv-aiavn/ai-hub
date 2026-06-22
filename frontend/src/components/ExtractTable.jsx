import React, { useEffect, useMemo, useState } from "react";
import Icon from "./Icon.jsx";
import { listGcn, listBatches } from "../api.js";
import { subscribeEvents } from "../events.js";

const STATUS_LABEL = {
  queued: "Chờ", processing: "Đang xử lý", done: "Xong", error: "Lỗi", skip: "Bỏ qua",
};
const REVIEW_LABEL = {
  unreviewed: "Chưa kiểm", needs_review: "Cần xem", reviewed: "Đã duyệt",
};

export default function ExtractTable({ batchId, onPickBatch, onOpen }) {
  const [batches, setBatches] = useState([]);
  const [rows, setRows] = useState([]);
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);

  async function refresh() {
    setLoading(true);
    try {
      const d = await listGcn({ batchId, status: status || undefined, q: q.trim() || undefined });
      setRows(d.gcn || []);
    } catch { /* bỏ qua */ } finally { setLoading(false); }
  }

  useEffect(() => { listBatches().then((d) => setBatches(d.batches || [])).catch(() => {}); }, []);
  useEffect(() => { refresh(); /* eslint-disable-next-line */ }, [batchId, status]);

  // Live: mọi event gcn/batch → refresh nhẹ (debounce).
  useEffect(() => {
    let t = null;
    const un = subscribeEvents(() => { clearTimeout(t); t = setTimeout(refresh, 600); });
    return () => { un(); clearTimeout(t); };
    // eslint-disable-next-line
  }, [batchId, status, q]);

  // Gom theo group_key (Số phát hành). Hàng cùng nhóm dính nhau, có vạch phân nhóm.
  const groups = useMemo(() => {
    const map = new Map();
    for (const r of rows) {
      const k = r.group_key || "(chưa có Số phát hành)";
      if (!map.has(k)) map.set(k, []);
      map.get(k).push(r);
    }
    return [...map.entries()];
  }, [rows]);

  return (
    <div className="panel extract-table">
      <div className="et-toolbar">
        <h2>Bảng trích xuất</h2>
        <div className="et-filters">
          <select value={batchId || ""} onChange={(e) => onPickBatch?.(e.target.value || null)}>
            <option value="">Tất cả lô</option>
            {batches.map((b) => (
              <option key={b.batch_id} value={b.batch_id}>{b.name} · {b.file_count} giấy</option>
            ))}
          </select>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Mọi trạng thái</option>
            {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <div className="search-box">
            <Icon name="search" size={15} />
            <input placeholder="Tìm Số phát hành / tên tệp" value={q}
              onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && refresh()} />
          </div>
          <button className="ghost sm" onClick={refresh}><Icon name="refresh" size={14} /> Làm mới</button>
        </div>
      </div>

      <div className="et-scroll">
        <table className="et-grid">
          <thead>
            <tr>
              <th>Tên / Tệp</th><th>Trạng thái</th><th>Số phát hành</th><th>Số vào sổ</th>
              <th>Ngày cấp</th><th>Chủ sử dụng</th><th>Tờ bản đồ</th><th>Trang</th>
              <th>#GCN</th><th>Hậu kiểm</th>
            </tr>
          </thead>
          <tbody>
            {groups.map(([key, items]) => (
              <React.Fragment key={key}>
                <tr className="group-head">
                  <td colSpan={10}><Icon name="layers" size={13} /> {key} · {items.length} bản</td>
                </tr>
                {items.map((r) => {
                  const s = r.summary || {};
                  return (
                    <tr key={r.gcn_id} className="et-row" onClick={() => onOpen?.(r.gcn_id)}>
                      <td className="et-name">{r.display_name || r.filename}</td>
                      <td><span className={`badge st-${r.status}`}>{STATUS_LABEL[r.status] || r.status}</span></td>
                      <td>{s.so_phat_hanh || r.group_key || "—"}</td>
                      <td>{s.so_vao_so || "—"}</td>
                      <td>{s.ngay_cap || "—"}</td>
                      <td className="et-chu">{(s.chu_su_dung || []).join(", ") || "—"}</td>
                      <td>{(s.to_ban_do || []).join(", ") || "—"}</td>
                      <td>{r.page_count || 0}</td>
                      <td>{s.gcn_count || 0}</td>
                      <td><span className={`badge rv-${r.review_status}`}>{REVIEW_LABEL[r.review_status] || r.review_status}</span></td>
                    </tr>
                  );
                })}
              </React.Fragment>
            ))}
            {!rows.length && (
              <tr><td colSpan={10} className="muted center">
                {loading ? "Đang tải…" : "Chưa có GCN. Tạo việc để bắt đầu."}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
