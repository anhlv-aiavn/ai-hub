import React, { useEffect, useState } from "react";
import Icon from "./Icon.jsx";
import { listGcn, getBranches } from "../api.js";
import { subscribeEvents } from "../events.js";
import { toastErr } from "../toast.js";

// Hàng chờ hậu kiểm (PLAN_.md §Vòng đời sau xử lý ③, §Frontend 4) — liệt kê hồ
// sơ đã xử lý xong nhưng chưa/cần hậu kiểm, ưu tiên nghi trùng trước. "Lấy việc"
// điều hướng vào Reconcile.jsx — khóa thật sự do Reconcile tự claim khi mount.
export default function ReviewQueue({ user, onOpen }) {
  const isAdmin = user?.role === "admin";
  const [branch, setBranch] = useState("");
  const [branches, setBranches] = useState([]);
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);

  async function refresh() {
    setLoading(true);
    try {
      const [a, b] = await Promise.all([
        listGcn({ status: "done", review: "unreviewed", branch: branch || undefined, limit: 500 }),
        listGcn({ status: "done", review: "needs_review", branch: branch || undefined, limit: 500 }),
      ]);
      const byId = new Map();
      for (const r of [...(a.gcn || []), ...(b.gcn || [])]) {
        if (!byId.has(r.gcn_id)) byId.set(r.gcn_id, r);
      }
      const list = [...byId.values()];
      list.sort((x, y) => (
        (y.dup_suspect ? 1 : 0) - (x.dup_suspect ? 1 : 0)
      ) || String(x.created_at || "").localeCompare(String(y.created_at || "")));
      setRows(list);
    } catch (e) { toastErr(e.message || e); } finally { setLoading(false); }
  }

  useEffect(() => {
    if (isAdmin) getBranches().then((d) => setBranches(d.branches || [])).catch(() => {});
  }, [isAdmin]);
  useEffect(() => { refresh(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [branch]);
  useEffect(() => {
    let t = null;
    const un = subscribeEvents(() => { clearTimeout(t); t = setTimeout(refresh, 800); });
    return () => { un(); clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [branch]);

  return (
    <div className="panel review-queue">
      <div className="et-toolbar">
        <h2>Hàng chờ hậu kiểm <span className="mb-count">{rows.length}</span></h2>
        <div className="et-filters">
          {isAdmin && (
            <select value={branch} onChange={(e) => setBranch(e.target.value)}>
              <option value="">Tất cả chi nhánh</option>
              {branches.map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
          )}
          <button className="ghost sm" onClick={refresh}><Icon name="refresh" size={14} /> Làm mới</button>
        </div>
      </div>

      <div className="mb-list tbl-dense">
        {loading && Array.from({ length: 6 }).map((_, i) => <div key={i} className="file-row skeleton" />)}

        {!loading && !rows.length && (
          <div className="mb-empty">
            <Icon name="checkCircle" size={30} />
            <p>Không còn hồ sơ chờ hậu kiểm.</p>
          </div>
        )}

        {!loading && rows.map((r) => (
          <div key={r.gcn_id} className="file-row" role="button" tabIndex={0}
            onClick={() => onOpen?.(r.gcn_id)}
            onKeyDown={(e) => { if (e.key === "Enter") onOpen?.(r.gcn_id); }}>
            <span className="fr-check" />
            <Icon name="fileText" size={16} className="fr-ico" />
            <span className="fr-name" title={r.display_name || r.filename}>
              {r.display_name || r.filename}
              {r.dup_suspect && (
                <span className="dup-flag" title="Nghi trùng nội dung">
                  <Icon name="alertTriangle" size={12} />
                </span>
              )}
            </span>
            <span className="fr-meta">{r.branch || "—"}</span>
            <button type="button" className="ghost xs"
              onClick={(e) => { e.stopPropagation(); onOpen?.(r.gcn_id); }}>
              Lấy việc
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
