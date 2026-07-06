import React, { useEffect } from "react";
import Icon from "./Icon.jsx";

// Modal full-screen chuẩn (tái dùng đúng pattern preview file trong ExportView.jsx):
// backdrop mờ che toàn màn hình + card căn giữa, tự cuộn nội bộ. Đóng khi click
// backdrop hoặc bấm Escape.
export default function Modal({ title, onClose, children, wide = false }) {
  useEffect(() => {
    function onKey(e) { if (e.key === "Escape") onClose?.(); }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className={`modal-card ${wide ? "wide" : ""}`} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <b>{title}</b>
          <button className="icon-btn" onClick={onClose} aria-label="Đóng"><Icon name="x" size={16} /></button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}
