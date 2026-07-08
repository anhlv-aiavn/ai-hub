import React, { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Icon from "./Icon.jsx";
import Pager from "./Pager.jsx";
import { listGcn, listBatches, getBranches } from "../api.js";
import { subscribeEvents } from "../events.js";

const PAGE_SIZE = 50;
const PENDING = new Set(["queued", "processing"]);

export const STATUS_LABEL = {
  queued: "Chờ", processing: "Đang xử lý", done: "Xong", error: "Lỗi", skip: "Bỏ qua",
};
export const REVIEW_LABEL = {
  unreviewed: "Chưa kiểm", needs_review: "Không duyệt", reviewed: "Đã duyệt",
};
const REVIEW_FILTER = { "": "Mọi hậu kiểm", ...REVIEW_LABEL };

function fmtDupDate(v) {
  if (!v) return "";
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric" });
}

const DUP_POP_WIDTH = 300; // khớp min-width ở CSS .dup-pop — dùng để tự kẹp trong viewport

// Cờ "Nghi trùng" bấm ra danh sách CỤ THỂ hồ sơ nghi trùng (tên/lô/ngày/trạng
// thái) — bấm 1 hồ sơ để nhảy thẳng sang đối soát, thay vì chỉ biết "trùng với
// N hồ sơ khác" như trước (không biết là hồ sơ nào để mà so).
//
// Popover portal thẳng ra <body>, định vị bằng toạ độ THẬT của nút bấm
// (getBoundingClientRect), KHÔNG đặt position:absolute lồng trong <tr>/<td>.
// Bảng nằm trong khung cuộn riêng (.et-scroll { overflow:auto }) và hàng
// dưới không có position/z-index nào cả, nên tuy popover đặt z-index cao,
// stacking context của nó vẫn bị "giam" trong bảng → hàng dưới vẫn có thể vẽ
// đè lên (bug thực tế: badge bấm ra popover nhưng bị dòng tên hồ sơ bên dưới
// che mất). Portal ra body thoát hẳn khỏi stacking context/overflow của bảng.
function DupFlag({ candidates, onOpen }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState(null); // {top,left} toạ độ viewport, null = chưa đo
  const btnRef = useRef(null);
  const popRef = useRef(null);

  function openPop() {
    const r = btnRef.current?.getBoundingClientRect();
    if (!r) return;
    const left = Math.min(r.left, window.innerWidth - DUP_POP_WIDTH - 10);
    setPos({ top: r.bottom + 6, left: Math.max(8, left) });
    setOpen(true);
  }

  useEffect(() => {
    if (!open) return;
    function onDoc(e) {
      if (btnRef.current?.contains(e.target) || popRef.current?.contains(e.target)) return;
      setOpen(false);
    }
    // Cuộn bảng/trang hoặc resize cửa sổ → toạ độ đã đo không còn đúng nữa,
    // đóng luôn cho đơn giản (thay vì phải theo dõi lại vị trí liên tục). Cuộn
    // BÊN TRONG popover (danh sách nhiều hồ sơ nghi trùng, .dup-pop có overflow-y
    // riêng) thì bỏ qua — không thì vừa lướt xem danh sách là popover tự đóng
    // ngay (bug thực tế: nhiều nội dung hiện thanh cuộn, cuộn phát là mất popover).
    function onScroll(e) {
      if (popRef.current?.contains(e.target)) return;
      setOpen(false);
    }
    function onResize() { setOpen(false); }
    document.addEventListener("mousedown", onDoc, true);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onResize);
    return () => {
      document.removeEventListener("mousedown", onDoc, true);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onResize);
    };
  }, [open]);

  return (
    <span className="dup-flag-wrap">
      <button type="button" ref={btnRef} className="dup-flag"
        onClick={(e) => { e.stopPropagation(); open ? setOpen(false) : openPop(); }}>
        <Icon name="alertTriangle" size={12} /> Nghi trùng ({candidates.length})
      </button>
      {open && pos && createPortal(
        <div className="dup-pop" ref={popRef} style={{ top: pos.top, left: pos.left }}
          onClick={(e) => e.stopPropagation()}>
          <div className="dup-pop-title">Nghi trùng nội dung với:</div>
          {candidates.map((c) => (
            <button type="button" key={c.gcn_id} className="dup-pop-item"
              onClick={() => { setOpen(false); onOpen?.(c.gcn_id); }}>
              <span className="dpi-name">{c.filename || c.gcn_id}</span>
              <span className="dpi-meta">{fmtDupDate(c.created_at)} · {STATUS_LABEL[c.status] || c.status || "?"}</span>
            </button>
          ))}
        </div>,
        document.body,
      )}
    </span>
  );
}

export default function ExtractTable({ user, batchId, onPickBatch, onOpen, initialStatus = "" }) {
  const isAdmin = user?.role === "admin";
  const [batches, setBatches] = useState([]);
  const [branches, setBranches] = useState([]);
  const [branch, setBranch] = useState("");
  const [rows, setRows] = useState([]);
  const [status, setStatus] = useState(initialStatus);
  const [review, setReview] = useState("");
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [hasPending, setHasPending] = useState(false);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(1);
  const refreshRef = useRef(() => {});

  async function refresh(p = page) {
    setLoading(true);
    try {
      const d = await listGcn({
        batchId, branch: branch || undefined, status: status || undefined,
        review: review || undefined, q: q.trim() || undefined, page: p, pageSize: PAGE_SIZE,
      });
      const list = d.gcn || [];
      setRows(list);
      setTotal(d.total || 0);
      setTotalPages(d.total_pages || 1);
      setHasPending(list.some((r) => PENDING.has(r.status)));
    } catch { /* bỏ qua */ } finally { setLoading(false); }
  }
  useEffect(() => { refreshRef.current = refresh; });

  function refreshBatches() {
    listBatches().then((d) => setBatches(d.batches || [])).catch(() => {});
  }
  useEffect(() => {
    refreshBatches();
    if (isAdmin) getBranches().then((d) => setBranches(d.branches || [])).catch(() => {});
  }, [isAdmin]);
  useEffect(() => { setStatus(initialStatus); }, [initialStatus]);
  // Đổi bộ lọc → về trang 1 (không dùng state `page` cũ để tránh closure lệch nhịp).
  // Đổi batchId (kể cả khi vừa tạo đợt mới ở "Số hóa") → cũng nạp lại danh sách đợt
  // để số lượng hồ sơ hiển thị đúng ngay, không cần tải lại trang.
  useEffect(() => { setPage(1); refresh(1); refreshBatches(); /* eslint-disable-next-line */ }, [batchId, branch, status, review]);

  function goToPage(p) { setPage(p); refresh(p); }
  function runSearch() { setPage(1); refresh(1); }

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
          <select value={review} onChange={(e) => setReview(e.target.value)}>
            {Object.entries(REVIEW_FILTER).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <div className="search-box" title="Tìm theo số phát hành, số tờ, số thửa, số vào sổ, tên hồ sơ, tên file GCN, chủ sử dụng">
            <Icon name="search" size={15} />
            <input
              placeholder="Tìm số phát hành, số tờ, số thửa, …"
              value={q}
              onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && runSearch()} />
          </div>
          <button className="ghost sm" onClick={() => refresh()}><Icon name="refresh" size={14} /> Tìm kiếm</button>
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
                      {f.dup_suspect && (
                        <DupFlag candidates={f.dup_candidates || []} onOpen={onOpen} />
                      )}
                      {f.locked_by && (
                        <span className="lock-flag" title="Đang được hậu kiểm — mở ra sẽ ở chế độ chỉ xem">
                          <Icon name="clock" size={12} /> Đang hậu kiểm: {f.locked_by}
                        </span>
                      )}
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

      <Pager page={page} totalPages={totalPages} total={total} unit="file" onChange={goToPage} />
    </div>
  );
}
