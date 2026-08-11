import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

// Dùng chung giữa trang quản trị "QC Sync" (QcSync.jsx) và thống kê "Tổng
// quan" (QcSyncStats.jsx) — tránh lặp lại nhãn/màu verdict + logic hiện chi
// tiết lý do QC ở 2 nơi.

export const VERDICT_LABEL = { pass: "Đạt", warn: "Đạt (cảnh báo)", fail: "Không đạt" };
export const VERDICT_CLASS = { pass: "dot-ok", warn: "dot-warn", fail: "dot-err" };

// Dịch mã lý do QC (`qc.reasons[].code`, hợp đồng qc-scanner-server đọc lúc
// dựng `app/qc_client.py`) sang tiếng Việt — CHỈ các mã đã xác nhận từ tài
// liệu API. Mã lạ/chưa có trong danh sách → hiện nguyên mã + `message` API
// trả về, KHÔNG tự bịa nghĩa (tránh dịch sai gây hiểu lầm).
export const REASON_VI = {
  CLIPPED_EDGE: "Ảnh bị cắt mất cạnh/góc",
  BLURRY: "Ảnh bị mờ, không đủ nét",
  GLARE: "Ảnh bị chói/loá sáng",
  EXTREME_SKEW: "Ảnh bị nghiêng quá mức",
};

export function reasonLabel(reason) {
  return REASON_VI[reason?.code] || reason?.message || reason?.code || "Không rõ lý do";
}

// Badge verdict (chấm màu + nhãn) — hover HOẶC bấm vào sẽ hiện popover liệt
// kê từng lý do QC: mã gốc + tên tiếng Việt (+ gợi ý nếu API có trả `hint`).
// Bấm để "ghim" mở (đóng khi bấm ra ngoài/Esc) — hữu ích khi cần đọc lâu hoặc
// dùng trên thiết bị cảm ứng (không có hover).
//
// Popover render qua PORTAL vào document.body (position:fixed, toạ độ tự
// tính bằng getBoundingClientRect) — KHÔNG lồng trong DOM của badge, vì badge
// nằm trong bảng `.tbl-dense` có `overflow:hidden` (bo góc); nếu định vị
// tương đối bình thường, popover ở gần đáy/mép bảng sẽ bị CẮT MẤT.
export function VerdictBadge({ verdict, reasons }) {
  const [pinned, setPinned] = useState(false);
  const [hovering, setHovering] = useState(false);
  const [pos, setPos] = useState(null);
  const ref = useRef(null);
  const list = reasons || [];
  const open = (pinned || hovering) && list.length > 0;

  function updatePos() {
    const r = ref.current?.getBoundingClientRect();
    if (r) setPos({ left: r.left, top: r.bottom + 6 });
  }

  useEffect(() => {
    if (!pinned) return;
    function onDoc(e) { if (!ref.current?.contains(e.target)) setPinned(false); }
    function onKey(e) { if (e.key === "Escape") setPinned(false); }
    document.addEventListener("mousedown", onDoc, true);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDoc, true);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [pinned]);

  if (!verdict) return null;

  return (
    <span className="qc-verdict" ref={ref}
      onMouseEnter={() => { setHovering(true); updatePos(); }} onMouseLeave={() => setHovering(false)}>
      <button type="button" className="qc-verdict-trigger" disabled={!list.length}
        aria-haspopup={list.length ? "true" : undefined} aria-expanded={open}
        onClick={() => { if (!list.length) return; updatePos(); setPinned((v) => !v); }}>
        <span className={`dot ${VERDICT_CLASS[verdict] || "dot-unknown"}`} />
        {VERDICT_LABEL[verdict] || verdict}
      </button>
      {open && pos && createPortal(
        <div className="qc-verdict-pop" role="tooltip" style={{ left: pos.left, top: pos.top }}>
          {list.map((r, i) => (
            <div className="qc-verdict-reason" key={i}>
              <div><code>{r.code}</code> — {reasonLabel(r)}</div>
              {r.hint && <div className="muted small">{r.hint}</div>}
            </div>
          ))}
        </div>,
        document.body,
      )}
    </span>
  );
}
