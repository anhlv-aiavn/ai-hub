import React, { useEffect, useMemo, useRef, useState } from "react";
import Icon from "./Icon.jsx";
import { listGcn, listBatches, getBranches } from "../api.js";
import { subscribeEvents } from "../events.js";

const PENDING = new Set(["queued", "processing"]);

const STATUS_LABEL = {
  queued: "Chờ", processing: "Đang xử lý", done: "Xong", error: "Lỗi", skip: "Bỏ qua",
};
const REVIEW_LABEL = {
  unreviewed: "Chưa kiểm", needs_review: "Cần xem", reviewed: "Đã duyệt",
};

export default function ExtractTable({ user, batchId, onPickBatch, onOpen, initialStatus = "" }) {
  const isAdmin = user?.role === "admin";
  const [batches, setBatches] = useState([]);
  const [branches, setBranches] = useState([]);
  const [branch, setBranch] = useState("");
  const [rows, setRows] = useState([]);
  const [status, setStatus] = useState(initialStatus);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [hasPending, setHasPending] = useState(false);
  const refreshRef = useRef(() => {});

  async function refresh() {
    setLoading(true);
    try {
      const d = await listGcn({ batchId, branch: branch || undefined, status: status || undefined, q: q.trim() || undefined });
      const list = d.gcn || [];
      setRows(list);
      setHasPending(list.some((r) => PENDING.has(r.status)));
    } catch { /* bỏ qua */ } finally { setLoading(false); }
  }
  useEffect(() => { refreshRef.current = refresh; });

  useEffect(() => {
    listBatches().then((d) => setBatches(d.batches || [])).catch(() => {});
    if (isAdmin) getBranches().then((d) => setBranches(d.branches || [])).catch(() => {});
  }, [isAdmin]);
  useEffect(() => { setStatus(initialStatus); }, [initialStatus]);
  useEffect(() => { refresh(); /* eslint-disable-next-line */ }, [batchId, branch, status]);

  // Live: SSE đẩy tức thì + polling dự phòng khi còn giấy đang chạy (chắc ăn).
  useEffect(() => {
    let t = null;
    const un = subscribeEvents(() => { clearTimeout(t); t = setTimeout(() => refreshRef.current(), 500); });
    return () => { un(); clearTimeout(t); };
  }, []);

  useEffect(() => {
    if (!hasPending) return;
    const id = setInterval(() => refreshRef.current(), 3000);
    return () => clearInterval(id);
  }, [hasPending]);

  // Gom theo FILE NGUỒN (gcn_id): 1 file → nhiều bản cắt (GCN). Header = tên file +
  // số giấy; dòng con là từng bản cắt thụt vào.
  const groups = useMemo(() => {
    const map = new Map();
    for (const r of rows) {
      const k = r.gcn_id;
      if (!map.has(k)) map.set(k, []);
      map.get(k).push(r);
    }
    return [...map.values()];
  }, [rows]);

  return (
    <div className="panel extract-table">
      <div className="et-toolbar">
        <h2>Hồ sơ đã xử lý</h2>
        <div className="et-filters">
          {isAdmin && (
            <select value={branch} onChange={(e) => setBranch(e.target.value)}>
              <option value="">Tất cả chi nhánh</option>
              {branches.map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
          )}
          <select value={batchId || ""} onChange={(e) => onPickBatch?.(e.target.value || null)}>
            <option value="">Tất cả đợt</option>
            {batches.map((b) => (
              <option key={b.batch_id} value={b.batch_id}>{b.name} · {b.file_count} hồ sơ</option>
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
              <th>Tên / Tệp</th><th>Trạng thái</th><th>Số phát hành</th><th>Số tờ</th>
              <th>Số thửa</th><th>Ngày cấp</th><th>Chủ sử dụng</th><th>Số vào sổ</th>
              <th>Hậu kiểm</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((items) => {
              const f = items[0];
              const multi = items.length > 1;
              return (
                <React.Fragment key={f.gcn_id}>
                  <tr className="group-head">
                    <td colSpan={9}>
                      <Icon name="layers" size={13} /> {f.display_name || f.filename}
                      <span className="gh-count">{multi ? `${items.length} giấy chứng nhận` : "1 giấy chứng nhận"}</span>
                    </td>
                  </tr>
                  {items.map((r) => {
                    const s = r.summary || {};
                    return (
                      <tr key={r.row_id || r.gcn_id} className="et-row" onClick={() => onOpen?.(r.gcn_id)}>
                        <td className={`et-name ${multi ? "child" : ""}`}>
                          {multi
                            ? `↳ ${r.cut_name || `Bản cắt ${s.gcn_pos || 1}`}`
                            : (r.display_name || r.cut_name || r.filename)}
                        </td>
                        <td><span className={`badge st-${r.status}`}>{STATUS_LABEL[r.status] || r.status}</span></td>
                        <td>{s.so_phat_hanh || r.group_key || "—"}</td>
                        <td>{(s.to_ban_do || []).join(", ") || "—"}</td>
                        <td>{(s.so_thua || []).join(", ") || "—"}</td>
                        <td>{s.ngay_cap || "—"}</td>
                        <td className="et-chu">{(s.chu_su_dung || []).join(", ") || "—"}</td>
                        <td>{s.so_vao_so || "—"}</td>
                        <td><span className={`badge rv-${r.review_status}`}>{REVIEW_LABEL[r.review_status] || r.review_status}</span></td>
                      </tr>
                    );
                  })}
                </React.Fragment>
              );
            })}
            {!rows.length && (
              <tr><td colSpan={9} className="muted center">
                {loading ? "Đang tải…" : "Chưa có hồ sơ. Vào mục Số hóa để bắt đầu."}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
