import React, { useEffect, useRef, useState } from "react";
import Icon from "./Icon.jsx";
import { pageImageUrl, getPageInfo, cutPageImageUrl, getCutPageInfo } from "../api.js";

const ZOOM_MIN = 1;
const ZOOM_MAX = 4;
const ZOOM_STEP = 0.25;
const clampZoom = (z) => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, +z.toFixed(2)));

// Ảnh trang PDF render ở server (1 <img>/trang, lazy-load). Trang 1-indexed;
// backend page index 0-indexed → n = p-1. cutIndex != null → xem FILE CẮT.
export default function GcnPdf({ gcnId, cutIndex = null, page = 1, onPageChange }) {
  const [numPages, setNumPages] = useState(0);
  const [err, setErr] = useState("");
  const [zoom, setZoom] = useState(1);
  const wrapRef = useRef(null);
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

  const cur = Math.min(Math.max(1, page || 1), numPages || 1);

  // Reset zoom khi đổi trang/tài liệu, để không giữ mức phóng to cũ nhầm sang ảnh khác.
  useEffect(() => { setZoom(1); }, [cur, gcnId, cutIndex]);

  // Giữ Ctrl + cuộn chuột để zoom tại chỗ. Gắn bằng addEventListener (không qua
  // props onWheel của React) vì React đăng ký wheel listener ở chế độ passive,
  // khiến preventDefault() không chặn được zoom trang mặc định của trình duyệt.
  // Dep [numPages]: lần render ĐẦU numPages=0 → component trả về sớm ("Đang tải
  // PDF…"), div gắn wrapRef CHƯA tồn tại nên wrapRef.current vẫn null lúc effect
  // chạy — nếu deps rỗng [] thì effect không bao giờ chạy lại để gắn listener
  // sau khi div render, khiến Ctrl+cuộn chuột rơi xuống zoom mặc định của trình
  // duyệt thay vì phóng to ảnh (bug thực tế: "zoom trang web chứ không zoom PDF").
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const onWheel = (e) => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      setZoom((z) => clampZoom(z + (e.deltaY < 0 ? ZOOM_STEP : -ZOOM_STEP)));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [numPages]);

  if (err) return <div className="pdf-pane error">Không tải được PDF: {err}</div>;
  if (!gcnId) return <div className="pdf-pane muted">Chưa chọn GCN.</div>;
  if (!numPages) return <div className="pdf-pane muted">Đang tải PDF…</div>;

  const go = (p) => onPageChange?.(Math.min(Math.max(1, p), numPages));
  const zoomIn = () => setZoom((z) => clampZoom(z + ZOOM_STEP));
  const zoomOut = () => setZoom((z) => clampZoom(z - ZOOM_STEP));
  const zoomReset = () => setZoom(1);

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
          <span className="pdf-zoom-group">
            <button className="icon-btn" onClick={zoomOut} disabled={zoom <= ZOOM_MIN} aria-label="Thu nhỏ"><Icon name="zoomOut" size={16} /></button>
            <button className="pdf-zoom-pct" onClick={zoomReset} title="Về 100%">{Math.round(zoom * 100)}%</button>
            <button className="icon-btn" onClick={zoomIn} disabled={zoom >= ZOOM_MAX} aria-label="Phóng to"><Icon name="zoomIn" size={16} /></button>
          </span>
        </div>
        <div className="pdf-canvas-wrap" ref={wrapRef}>
          <img className="pdf-page-img" key={cur} src={imgUrl(cur - 1, 1400)}
            alt={`Trang ${cur}`} title="Giữ Ctrl + cuộn chuột để phóng to"
            style={{ width: `${zoom * 100}%` }} />
        </div>
      </div>
    </div>
  );
}
