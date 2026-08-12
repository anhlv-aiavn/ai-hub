import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { qcSyncCutPdfUrl, qcSyncSourcePdfUrl } from "../api.js";
import Modal from "./Modal.jsx";

// Dùng chung giữa trang quản trị "QC Sync" (QcSync.jsx) và thống kê "Tổng
// quan" (QcSyncStats.jsx) — tránh lặp lại nhãn/màu verdict + logic hiện chi
// tiết lý do QC ở 2 nơi.

export const VERDICT_LABEL = { pass: "Đạt", warn: "Đạt (cảnh báo)", fail: "Không đạt" };
export const VERDICT_CLASS = { pass: "dot-ok", warn: "dot-warn", fail: "dot-err" };

// Dùng chung cho mọi bộ lọc trạng thái item (QcSync.jsx + QcSyncStats.jsx) —
// tránh định nghĩa lặp lại 2 nơi lệch nhau.
export const ITEM_STATUS_OPTIONS = [
  ["", "— Mọi trạng thái xử lý —"], ["error", "Lỗi"], ["no_file", "Không thấy file"],
  ["queued", "Đang chờ"], ["processing", "Đang xử lý"], ["done", "Xong"], ["no_gcn", "Không thấy GCN"],
];
export const VERDICT_OPTIONS = [
  ["", "— Mọi verdict QC —"], ["pass", "Đạt"], ["warn", "Đạt (cảnh báo)"], ["fail", "Không đạt"],
];

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

// Màu theo SEVERITY của từng lý do (khác màu verdict tổng — 1 lý do "warn" có
// thể nằm trong trang "fail" nếu trang đó còn lý do khác nặng hơn) — API trả
// severity "fail"/"warn" (đôi khi "error", coi cùng nhóm "fail" cho chắc).
function reasonSeverityClass(reason) {
  const sev = reason?.severity;
  if (sev === "fail" || sev === "error") return "dot-err";
  if (sev === "warn") return "dot-warn";
  return "dot-unknown";
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
export function VerdictBadge({ verdict, reasons, pages }) {
  const [pinned, setPinned] = useState(false);
  const [hovering, setHovering] = useState(false);
  const [pos, setPos] = useState(null);
  const ref = useRef(null);
  const list = reasons || [];
  // Breakdown theo TRANG chỉ đáng hiện khi có >1 trang (PDF nhiều trang) VÀ
  // chỉ trang KHÔNG "pass" mới cần liệt kê (trang đạt không có gì để xem).
  const badPages = (pages || []).length > 1
    ? (pages || []).filter((p) => p.verdict && p.verdict !== "pass") : [];
  const hasContent = list.length > 0 || badPages.length > 0;
  const open = (pinned || hovering) && hasContent;

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
      <button type="button" className="qc-verdict-trigger" disabled={!hasContent}
        aria-haspopup={hasContent ? "true" : undefined} aria-expanded={open}
        onClick={() => { if (!hasContent) return; updatePos(); setPinned((v) => !v); }}>
        <span className={`dot ${VERDICT_CLASS[verdict] || "dot-unknown"}`} />
        {VERDICT_LABEL[verdict] || verdict}
      </button>
      {open && pos && createPortal(
        <div className="qc-verdict-pop" role="tooltip" style={{ left: pos.left, top: pos.top }}>
          {badPages.length > 0 && (
            <div className="qc-verdict-pages">
              {badPages.map((p) => (
                <span key={p.page} className="qc-verdict-page-chip">
                  <span className={`dot ${VERDICT_CLASS[p.verdict] || "dot-unknown"}`} />
                  Trang {p.page}: {VERDICT_LABEL[p.verdict] || p.verdict}
                </span>
              ))}
            </div>
          )}
          {list.map((r, i) => (
            <div className="qc-verdict-reason" key={i}>
              <div>
                <span className={`dot ${reasonSeverityClass(r)}`} /> <code>{r.code}</code> — {reasonLabel(r)}
              </div>
              {r.hint && <div className="muted small">{r.hint}</div>}
            </div>
          ))}
        </div>,
        document.body,
      )}
    </span>
  );
}

// Modal so sánh PDF NGUỒN (trái) với file ĐÃ CẮT (phải) — thay cho việc mở
// file cắt ở tab trình duyệt mới (bấm vào là rời khỏi trang, phải quay lại
// mới xem tiếp được danh sách) — 2 khung xem cạnh nhau để đối chiếu ngay.
function CutCompareModal({ itemId, cut, onClose }) {
  return (
    <Modal title={`So sánh — ${cut.name}`} onClose={onClose} wide className="qc-compare-modal">
      <div className="qc-compare-grid">
        <div className="qc-compare-pane">
          <div className="qc-compare-pane-head">File gốc (nguồn)</div>
          <iframe title="PDF nguồn" src={qcSyncSourcePdfUrl(itemId)} />
        </div>
        <div className="qc-compare-pane">
          <div className="qc-compare-pane-head">File đã cắt</div>
          <iframe title="PDF đã cắt" src={qcSyncCutPdfUrl(itemId, cut.index)} />
        </div>
      </div>
    </Modal>
  );
}

// Danh sách "File đã cắt" — bấm mở modal SO SÁNH với file gốc (thay vì mở tab
// mới, xem CutCompareModal ở trên) — đánh dấu riêng file KHÔNG đọc được Số
// phát hành (`cut.so_phat_hanh` rỗng, khác `no_gcn`: vẫn tìm thấy + cắt được
// trang GCN, chỉ là không đọc ra số trên đó) để admin bấm xem trực tiếp thay
// vì phải dò cả danh sách.
export function CutLinks({ itemId, cuts }) {
  const [compareCut, setCompareCut] = useState(null);
  const list = cuts || [];
  if (!list.length) return <span className="muted small">—</span>;
  return (
    <>
      {list.map((cut) => {
        const missingSph = !cut.so_phat_hanh;
        return (
          <button key={cut.index} type="button" className={`qc-cut-link ${missingSph ? "qc-cut-nosph" : ""}`}
            onClick={() => setCompareCut(cut)}
            title={missingSph ? `Chưa đọc được Số phát hành — ${cut.name}` : `So sánh với file gốc: ${cut.name}`}>
            {missingSph && "⚠ "}{cut.name}
          </button>
        );
      })}
      {compareCut && <CutCompareModal itemId={itemId} cut={compareCut} onClose={() => setCompareCut(null)} />}
    </>
  );
}

// Thanh phân trang dùng chung: nút trước/sau + ô "nhảy tới trang" (nhập số,
// CLAMP về [1, số trang cuối] khi rời ô/Enter — không cho nhảy quá phạm vi) +
// tổng số bản ghi. `total`/`pageSize` BẮT BUỘC (API đã trả `total` ở cả 3 nơi
// dùng component này — Phân loại, QC Sync, Tổng quan) để tính được trang cuối.
export function Pager({ page, setPage, pageSize, total }) {
  const maxPage = Math.max(1, Math.ceil((total || 0) / pageSize));
  const [jumpValue, setJumpValue] = useState(String(page));
  useEffect(() => { setJumpValue(String(page)); }, [page]);

  function commitJump() {
    const n = parseInt(jumpValue, 10);
    const clamped = Number.isNaN(n) ? page : Math.min(Math.max(n, 1), maxPage);
    setJumpValue(String(clamped));
    if (clamped !== page) setPage(clamped);
  }

  return (
    <div className="row qc-pager" style={{ gap: 8, alignItems: "center", flexWrap: "wrap" }}>
      <button className="ghost xs" disabled={page <= 1} onClick={() => setPage(1)} title="Trang đầu">«</button>
      <button className="ghost xs" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>← Trang trước</button>
      <span className="muted small qc-pager-jump">
        Trang{" "}
        <input className="text-input qc-pager-input" type="number" min={1} max={maxPage} value={jumpValue}
          onChange={(e) => setJumpValue(e.target.value)} onBlur={commitJump}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commitJump(); } }} />
        {" "}/ {maxPage} · Tổng {(total || 0).toLocaleString("vi-VN")} bản ghi
      </span>
      <button className="ghost xs" disabled={page >= maxPage} onClick={() => setPage((p) => p + 1)}>Trang sau →</button>
      <button className="ghost xs" disabled={page >= maxPage} onClick={() => setPage(maxPage)} title="Trang cuối">»</button>
    </div>
  );
}
