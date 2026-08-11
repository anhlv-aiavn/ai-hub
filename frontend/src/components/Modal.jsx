import React, { useEffect } from "react";
import Icon from "./Icon.jsx";

// Modal full-screen chuẩn (tái dùng đúng pattern preview file trong ExportView.jsx):
// backdrop mờ che toàn màn hình + card căn giữa, tự cuộn nội bộ. Đóng bằng nút X
// hoặc Escape — KHÔNG đóng khi click ra backdrop (tránh mất dở dữ liệu đang nhập).
export default function Modal({ title, onClose, children, wide = false, className = "" }) {
  useEffect(() => {
    function onKey(e) { if (e.key === "Escape") onClose?.(); }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-overlay">
      <div className={`modal-card ${wide ? "wide" : ""} ${className}`.trim()}>
        <div className="modal-head">
          <b>{title}</b>
          <button className="icon-btn" onClick={onClose} aria-label="Đóng"><Icon name="x" size={16} /></button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}
