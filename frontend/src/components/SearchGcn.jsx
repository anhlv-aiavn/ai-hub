import React, { useEffect, useState } from "react";
import Icon from "./Icon.jsx";
import { searchGcn, getBranches } from "../api.js";

const STATUS_LABEL = {
  queued: "Chờ", processing: "Đang xử lý", done: "Xong", error: "Lỗi", skip: "Bỏ qua", dead: "Poison",
};

// Tra cứu quy mô lớn (PLAN_.md §Vòng đời sau xử lý ②) — cursor theo created_at,
// KHÔNG skip/limit sâu. Dense list cùng style MinioBrowser (§Chuẩn UI dense).
export default function SearchGcn({ user, onOpen }) {
  const isAdmin = user?.role === "admin";
  const [soPhatHanh, setSoPhatHanh] = useState("");
  const [status, setStatus] = useState("");
  const [branch, setBranch] = useState("");
  const [branches, setBranches] = useState([]);
  const [items, setItems] = useState([]);
  const [cursor, setCursor] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (isAdmin) getBranches().then((d) => setBranches(d.branches || [])).catch(() => {});
  }, [isAdmin]);

  async function runSearch(before) {
    if (!before) { setLoading(true); setError(""); } else { setLoadingMore(true); }
    try {
      const d = await searchGcn({
        soPhatHanh: soPhatHanh.trim() || undefined,
        status: status || undefined,
        branch: branch || undefined,
        before: before || undefined,
      });
      setItems((prev) => (before ? [...prev, ...(d.items || [])] : (d.items || [])));
      setCursor(d.next_cursor || null);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setLoading(false); setLoadingMore(false);
    }
  }

  useEffect(() => { runSearch(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  function onSubmit(e) { e.preventDefault(); runSearch(); }

  return (
    <div className="panel search-gcn">
      <div className="et-toolbar">
        <h2>Tra cứu</h2>
        <form className="et-filters" onSubmit={onSubmit}>
          {isAdmin && (
            <select value={branch} onChange={(e) => setBranch(e.target.value)}>
              <option value="">Tất cả chi nhánh</option>
              {branches.map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
          )}
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Mọi trạng thái</option>
            {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <div className="search-box">
            <Icon name="search" size={15} />
            <input placeholder="Số phát hành" value={soPhatHanh}
              onChange={(e) => setSoPhatHanh(e.target.value)} />
          </div>
          <button className="primary sm" type="submit"><Icon name="search" size={14} /> Tìm</button>
        </form>
      </div>

      <div className="mb-list tbl-dense">
        {loading && Array.from({ length: 6 }).map((_, i) => <div key={i} className="file-row skeleton" />)}

        {!loading && error && (
          <div className="mb-empty mb-error">
            <Icon name="alertTriangle" size={30} />
            <p>{error}</p>
            <button type="button" className="ghost sm" onClick={() => runSearch()}>Thử lại</button>
          </div>
        )}

        {!loading && !error && !items.length && (
          <div className="mb-empty">
            <Icon name="fileText" size={30} />
            <p>Không có kết quả khớp bộ lọc.</p>
          </div>
        )}

        {!loading && !error && items.map((it) => (
          <div key={it.gcn_id} className="file-row" role="button" tabIndex={0}
            onClick={() => onOpen?.(it.gcn_id)}
            onKeyDown={(e) => { if (e.key === "Enter") onOpen?.(it.gcn_id); }}>
            <span className="fr-check" />
            <Icon name="fileText" size={16} className="fr-ico" />
            <span className="fr-name" title={it.display_name || it.filename}>
              {it.display_name || it.filename}
              {it.dup_suspect && (
                <span className="dup-flag" title="Nghi trùng nội dung">
                  <Icon name="alertTriangle" size={12} />
                </span>
              )}
            </span>
            <span className={`badge st-${it.status}`}>{STATUS_LABEL[it.status] || it.status}</span>
            <span className="fr-meta">
              {it.branch || "—"} · {(it.extracted_so_phat_hanhs || []).join(", ") || it.group_key || "—"}
            </span>
          </div>
        ))}

        {!loading && !error && cursor && (
          <button type="button" className="mb-more" disabled={loadingMore} onClick={() => runSearch(cursor)}>
            {loadingMore ? "Đang tải…" : "Tải thêm"}
          </button>
        )}
      </div>
    </div>
  );
}
