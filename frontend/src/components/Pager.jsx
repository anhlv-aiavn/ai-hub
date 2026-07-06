import React from "react";
import Icon from "./Icon.jsx";

// Phân trang dạng số trang (khác kiểu "Tải thêm"/cursor ở Audit log, MinioBrowser).
// Rút gọn quanh trang hiện tại: 1 … p-1 p p+1 … N (tối đa 7 nút số + 2 mũi tên).
function pageList(page, totalPages) {
  const out = [];
  const add = (v) => out.push(v);
  const window = 1;
  add(1);
  if (page - window > 2) add("…");
  for (let p = Math.max(2, page - window); p <= Math.min(totalPages - 1, page + window); p++) add(p);
  if (page + window < totalPages - 1) add("…");
  if (totalPages > 1) add(totalPages);
  return out;
}

export default function Pager({ page, totalPages, onChange, total, unit = "kết quả" }) {
  if (totalPages <= 1) return null;
  return (
    <div className="pager">
      {total != null && <span className="pager-info">{total.toLocaleString("vi-VN")} {unit}</span>}
      <div className="pager-nav">
        <button type="button" className="pager-btn" disabled={page <= 1}
          onClick={() => onChange(page - 1)} aria-label="Trang trước">
          <Icon name="chevronLeft" size={14} />
        </button>
        {pageList(page, totalPages).map((p, i) => (
          p === "…"
            ? <span key={`e${i}`} className="pager-ellipsis">…</span>
            : (
              <button key={p} type="button" className={`pager-btn ${p === page ? "active" : ""}`}
                onClick={() => onChange(p)} aria-current={p === page ? "page" : undefined}>
                {p}
              </button>
            )
        ))}
        <button type="button" className="pager-btn" disabled={page >= totalPages}
          onClick={() => onChange(page + 1)} aria-label="Trang sau">
          <Icon name="chevronRight" size={14} />
        </button>
      </div>
    </div>
  );
}
