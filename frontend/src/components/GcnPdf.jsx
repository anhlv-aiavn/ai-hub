import React, { useEffect, useState } from "react";
import Icon from "./Icon.jsx";
import { pageImageUrl, getPageInfo, cutPageImageUrl, getCutPageInfo } from "../api.js";

// Ảnh trang PDF render ở server (1 <img>/trang, lazy-load). Trang 1-indexed;
// backend page index 0-indexed → n = p-1. cutIndex != null → xem FILE CẮT.
export default function GcnPdf({ gcnId, cutIndex = null, page = 1, onPageChange }) {
  const [numPages, setNumPages] = useState(0);
  const [err, setErr] = useState("");
  const isCut = cutIndex !== null && cutIndex !== undefined;
  const imgUrl = (n, w) => (isCut ? cutPageImageUrl(gcnId, cutIndex, n, w) : pageImageUrl(gcnId, n, w));

  useEffect(() => {
    if (!gcnId) return;
    let live = true;
    setErr("");
    const info = isCut ? getCutPageInfo(gcnId, cutIndex) : getPageInfo(gcnId);
    info
      .then((d) => { if (live) setNumPages(d.pages || 0); })
      .catch((e) => { if (live) setErr(String(e.message || e)); });
    return () => { live = false; };
  }, [gcnId, cutIndex]);

  if (err) return <div className="pdf-pane error">Không tải được PDF: {err}</div>;
  if (!gcnId) return <div className="pdf-pane muted">Chưa chọn GCN.</div>;
  if (!numPages) return <div className="pdf-pane muted">Đang tải PDF…</div>;

  const cur = Math.min(Math.max(1, page || 1), numPages);
  const go = (p) => onPageChange?.(Math.min(Math.max(1, p), numPages));

  return (
    <div className="pdf-pane">
      <div className="pdf-rail">
        {Array.from({ length: numPages }, (_, i) => i + 1).map((p) => (
          <div key={p} className={`thumb ${p === cur ? "active" : ""}`}
            onClick={() => go(p)} title={`Trang ${p}`}>
            <img loading="lazy" src={imgUrl(p - 1, 200)} alt={`Trang ${p}`} />
            <span className="thumb-n">{p}</span>
          </div>
        ))}
      </div>
      <div className="pdf-main">
        <div className="pdf-nav">
          <button className="icon-btn" onClick={() => go(cur - 1)} disabled={cur <= 1} aria-label="Trang trước"><Icon name="chevronLeft" size={18} /></button>
          <span>Trang <b>{cur}</b> / {numPages}</span>
          <button className="icon-btn" onClick={() => go(cur + 1)} disabled={cur >= numPages} aria-label="Trang sau"><Icon name="chevronRight" size={18} /></button>
        </div>
        <div className="pdf-canvas-wrap">
          <img className="pdf-page-img zoomable" key={cur} src={imgUrl(cur - 1, 1400)}
            alt={`Trang ${cur}`} title="Bấm để mở ảnh trang ở tab mới"
            onClick={() => window.open(imgUrl(cur - 1, 2200), "_blank", "noopener")} />
        </div>
      </div>
    </div>
  );
}
