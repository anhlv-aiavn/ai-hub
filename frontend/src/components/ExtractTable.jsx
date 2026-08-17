import React, { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Icon from "./Icon.jsx";
import Pager from "./Pager.jsx";
import ConfirmDialog from "./ConfirmDialog.jsx";
import Modal from "./Modal.jsx";
import GcnPdf from "./GcnPdf.jsx";
import { listGcn, listBatches, getStats, deleteGcn } from "../api.js";
import { subscribeEvents } from "../events.js";
import { toastOk, toastErr } from "../toast.js";

const PAGE_SIZE = 50;
const PENDING = new Set(["queued", "processing"]);

// Nhớ bộ lọc + trang đang xem qua sessionStorage — không chỉ dựa vào việc giữ
// component mounted (App.jsx ẩn bằng display:none khi mở Reconcile), phòng khi
// vẫn có đường remount khác (đổi tab, F5, v.v.) làm mất lựa chọn của người dùng.
const FILTERS_KEY = "et_filters_v1";
function loadFilters() {
  try { return JSON.parse(sessionStorage.getItem(FILTERS_KEY)) || {}; }
  catch { return {}; }
}
function saveFilters(f) {
  try { sessionStorage.setItem(FILTERS_KEY, JSON.stringify(f)); } catch { /* bỏ qua */ }
}

export const STATUS_LABEL = {
  queued: "Chờ", processing: "Đang xử lý", done: "Xong", error: "Lỗi",
  no_gcn: "Không có giấy", no_file: "Không có tệp", skip: "Bỏ qua",
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
function DupFlag({ current, candidates, onOpen }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState(null); // {top,left} toạ độ viewport, null = chưa đo
  const btnRef = useRef(null);
  const popRef = useRef(null);
  // Danh sách hiện ĐẦY ĐỦ cả nhóm, KỂ CẢ hồ sơ đang xem (không chỉ "N hồ sơ
  // khác") — bấm sang hồ sơ khác trong nhóm sẽ luôn thấy đúng 1 danh sách y hệt
  // (chỉ đổi mục nào được đánh dấu "đang xem"), thay vì mỗi hồ sơ tự thấy 1
  // danh sách khác nhau (thiếu chính nó) gây cảm giác "danh sách bị đổi".
  const group = useMemo(
    () => [current, ...candidates].sort((a, b) => new Date(a.created_at || 0) - new Date(b.created_at || 0)),
    [current, candidates],
  );

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
          <div className="dup-pop-title">Nhóm nghi trùng ({group.length} hồ sơ):</div>
          {group.map((c) => {
            const isCurrent = c.gcn_id === current.gcn_id;
            return (
              <button type="button" key={c.gcn_id}
                className={`dup-pop-item${isCurrent ? " is-current" : ""}`} disabled={isCurrent}
                onClick={() => { setOpen(false); onOpen?.(c.gcn_id); }}>
                <span className="dpi-name">
                  {isCurrent && <Icon name="checkCircle" size={12} />}
                  {c.display_name || c.filename || c.gcn_id}
                </span>
                <span className="dpi-meta">
                  {isCurrent ? "Đang xem" : `${fmtDupDate(c.created_at)} · ${STATUS_LABEL[c.status] || c.status || "?"}`}
                </span>
              </button>
            );
          })}
        </div>,
        document.body,
      )}
    </span>
  );
}

// Badge "+N" cho các file khớp NGOÀI file đầu tiên (đã hiện sẵn dạng link trong
// dòng hồ sơ cha) — bấm ra popover liệt kê TOÀN BỘ (kể cả file đầu) để chọn xem.
// Cùng cơ chế portal-ra-body với DupFlag (tránh bị hàng dưới che, xem ghi chú ở đó).
function SmapFlag({ items, onView }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState(null);
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
      <button type="button" ref={btnRef} className="smap-more"
        onClick={(e) => { e.stopPropagation(); open ? setOpen(false) : openPop(); }}>
        +{items.length - 1}
      </button>
      {open && pos && createPortal(
        <div className="dup-pop" ref={popRef} style={{ top: pos.top, left: pos.left }}
          onClick={(e) => e.stopPropagation()}>
          <div className="dup-pop-title">File khớp ở kho nguồn ({items.length}):</div>
          {items.map((m, i) => (
            <button type="button" key={i} className="dup-pop-item"
              onClick={() => { setOpen(false); onView(i); }}>
              <span className="dpi-name">
                <Icon name="fileText" size={12} /> {m.display_name || m.filepath}
              </span>
            </button>
          ))}
        </div>,
        document.body,
      )}
    </span>
  );
}

export default function ExtractTable({ user, batchId, onPickBatch, onOpen, initialStatus = "" }) {
  // Viewer bị server giới hạn CHỈ thấy hồ sơ chưa hậu kiểm + hồ sơ CHÍNH họ đã
  // hậu kiểm (xem `_viewer_own_or` ở backend) — lọc theo tài khoản khác vô nghĩa
  // với họ (luôn ra rỗng) nên ẩn hẳn dropdown, chỉ hiện cho operator/admin.
  const canFilterReviewer = user?.role !== "viewer";
  // Xóa cả hồ sơ (khác "xóa mềm 1 giấy chứng nhận con" trong Reconcile.jsx) —
  // admin + operator, giới hạn trong lô họ được gán (ensure_batch_access ở backend).
  const canDelete = user?.role === "admin" || user?.role === "operator";
  const [batches, setBatches] = useState([]);
  const [rows, setRows] = useState([]);
  const [pendingDelete, setPendingDelete] = useState(null); // {gcn_id, name} đang chờ xác nhận xóa
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [smapPreview, setSmapPreview] = useState(null); // {gcnId, smapIndex, title, page} đang xem file khớp
  const [status, setStatus] = useState(() => initialStatus || loadFilters().status || "");
  const [review, setReview] = useState(() => loadFilters().review || "");
  const [reviewer, setReviewer] = useState(() => loadFilters().reviewer || "");
  const [reviewers, setReviewers] = useState([]);
  const [canhBao, setCanhBao] = useState(() => loadFilters().canhBao || "");  // "" | "co" | "khong"
  const [q, setQ] = useState(() => loadFilters().q || "");
  const [loading, setLoading] = useState(false);
  const [hasPending, setHasPending] = useState(false);
  const [page, setPage] = useState(() => loadFilters().page || 1);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(1);
  const refreshRef = useRef(() => {});

  async function refresh(p = page) {
    setLoading(true);
    try {
      const d = await listGcn({
        batchId, status: status || undefined,
        review: review || undefined, reviewer: reviewer || undefined,
        canhBao: canhBao || undefined,
        q: q.trim() || undefined, page: p, pageSize: PAGE_SIZE,
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
  // Danh sách tài khoản để lọc lấy từ /stats.by_reviewer (chỉ những ai đã từng
  // Duyệt/Không duyệt ít nhất 1 lần) — không cần quyền admin như /v1/users.
  function refreshReviewers() {
    if (!canFilterReviewer) return;
    getStats({ batchId }).then((d) => {
      setReviewers((d.by_reviewer || []).map((r) => r.reviewer).filter(Boolean).sort());
    }).catch(() => {});
  }
  // Đổi bộ lọc → về trang 1 (không dùng state `page` cũ để tránh closure lệch nhịp).
  // Đổi batchId (kể cả khi vừa tạo đợt mới ở "Số hóa") → cũng nạp lại danh sách đợt
  // để số lượng hồ sơ hiển thị đúng ngay, không cần tải lại trang.
  // Lần mount ĐẦU TIÊN: giữ nguyên trang đã khôi phục từ sessionStorage (không ép
  // về trang 1) — chỉ những lần SAU, khi người dùng thực sự đổi bộ lọc, mới reset.
  const filtersMounted = useRef(false);
  useEffect(() => {
    if (!filtersMounted.current) {
      filtersMounted.current = true;
      refresh(page);
    } else {
      setPage(1);
      refresh(1);
    }
    refreshBatches();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batchId, status, review, reviewer, canhBao]);
  // Lưu lại bộ lọc + trang đang xem để khôi phục nếu component bị remount
  // (đổi tab, F5, v.v.) — xem ghi chú ở FILTERS_KEY.
  useEffect(() => {
    saveFilters({ status, review, reviewer, canhBao, q, page });
  }, [status, review, reviewer, canhBao, q, page]);
  // Danh sách tài khoản phụ thuộc đợt đang chọn — nạp lại khi đổi.
  useEffect(() => { refreshReviewers(); /* eslint-disable-next-line */ }, [batchId, canFilterReviewer]);

  function goToPage(p) { setPage(p); refresh(p); }
  function runSearch() { setPage(1); refresh(1); }
  function openSmapPreview(gcnId, smapIndex, title) {
    setSmapPreview({ gcnId, smapIndex, title, page: 1 });
  }

  async function confirmDeleteGcn() {
    if (!pendingDelete) return;
    setDeleteBusy(true);
    try {
      await deleteGcn(pendingDelete.gcn_id);
      toastOk(`Đã xóa "${pendingDelete.name}"`);
      setPendingDelete(null);
      refresh();
    } catch (e) { toastErr(e.message || e); } finally { setDeleteBusy(false); }
  }

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
  const sttOffset = (page - 1) * PAGE_SIZE;

  return (
    <div className="panel extract-table">
      <div className="et-toolbar">
        <h2>Hồ sơ đã xử lý</h2>
        <div className="et-filters">
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
          {canFilterReviewer && (
            <select value={reviewer} onChange={(e) => setReviewer(e.target.value)}
              title="Lọc theo tài khoản đã Duyệt/Không duyệt — kết hợp với bộ lọc Hậu kiểm">
              <option value="">Mọi tài khoản</option>
              {reviewers.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          )}
          <select value={canhBao} onChange={(e) => setCanhBao(e.target.value)}
            title="Hồ sơ có chuyển nhượng nhưng không xác định được chủ mới — cần chuyên viên xác minh">
            <option value="">Mọi cảnh báo</option>
            <option value="co">⚠ Cần xác minh chủ cuối</option>
            <option value="khong">Không có cảnh báo</option>
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
              <th className="et-stt">STT</th>
              <th>Tên / Tệp</th><th>Trạng thái</th><th>Số phát hành</th><th>Số tờ</th>
              <th>Số thửa</th><th>Ngày cấp</th><th>Chủ sử dụng</th><th>Chủ cuối</th><th>Số vào sổ</th>
              <th>Hậu kiểm</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((items, gi) => {
              const f = items[0];
              const multi = items.length > 1;
              return (
                <React.Fragment key={f.gcn_id}>
                  <tr className="group-head">
                    <td className="et-stt">{sttOffset + gi + 1}</td>
                    <td colSpan={10}>
                      <Icon name="layers" size={13} /> {f.display_name || f.filename}
                      <span className="gh-count">{multi ? `${items.length} giấy chứng nhận` : "1 giấy chứng nhận"}</span>
                      {f.dup_suspect && (
                        <DupFlag
                          current={{ gcn_id: f.gcn_id, filename: f.filename, display_name: f.display_name,
                            created_at: f.created_at, status: f.status }}
                          candidates={f.dup_candidates || []} onOpen={onOpen}
                        />
                      )}
                      {f.locked_by && (
                        <span className="lock-flag" title="Đang được hậu kiểm — mở ra sẽ ở chế độ chỉ xem">
                          <Icon name="clock" size={12} /> Đang hậu kiểm: {f.locked_by}
                        </span>
                      )}
                      {f.s3_key_mapping?.length > 0 && (
                        <span className="gh-smap">
                          <button type="button" className="link-btn"
                            onClick={(e) => {
                              e.stopPropagation();
                              openSmapPreview(f.gcn_id, 0, f.s3_key_mapping[0].display_name);
                            }}>
                            <Icon name="fileText" size={12} className="ico" /> {f.s3_key_mapping[0].display_name}
                          </button>
                          {f.s3_key_mapping.length > 1 && (
                            <SmapFlag items={f.s3_key_mapping}
                              onView={(i) => openSmapPreview(f.gcn_id, i, f.s3_key_mapping[i].display_name)} />
                          )}
                        </span>
                      )}
                      {canDelete && (
                        <button type="button" className="icon-btn gh-del" title="Xóa hồ sơ này"
                          onClick={(e) => {
                            e.stopPropagation();
                            setPendingDelete({ gcn_id: f.gcn_id, name: f.display_name || f.filename, status: f.status });
                          }}>
                          <Icon name="trash" size={13} />
                        </button>
                      )}
                    </td>
                  </tr>
                  {items.map((r) => {
                    const s = r.summary || {};
                    return (
                      <tr key={r.row_id || r.gcn_id} className="et-row" onClick={() => onOpen?.(r.gcn_id)}>
                        <td className="et-stt" />
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
                        <td className="et-chu">{(() => {
                          const cc = s.chu_cuoi;
                          if (!cc) return "—";
                          if (cc.canh_bao) return (
                            <span className="cc-warn" title="Có chuyển nhượng nhưng chưa rõ chủ — cần xác minh">
                              <Icon name="alertTriangle" size={12} /> Chưa rõ chủ
                            </span>
                          );
                          const names = (cc.chu || []).filter(Boolean).join(", ");
                          if (!names) return "—";
                          return (
                            <span title={cc.nguon === "bien_dong" ? `Từ biến động${cc.thoi_gian ? " " + cc.thoi_gian : ""}` : "Chủ trên giấy gốc"}>
                              {names}
                              {cc.nguon === "bien_dong" && <span className="cc-tag"> ↻</span>}
                            </span>
                          );
                        })()}</td>
                        <td>{s.so_vao_so || "—"}</td>
                        <td><span className={`badge rv-${r.review_status}`}>{REVIEW_LABEL[r.review_status] || r.review_status}</span></td>
                      </tr>
                    );
                  })}
                </React.Fragment>
              );
            })}
            {!rows.length && (
              <tr><td colSpan={11} className="muted center">
                {loading ? "Đang tải…" : "Chưa có hồ sơ. Vào mục Số hóa để bắt đầu."}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      <Pager page={page} totalPages={totalPages} total={total} unit="file" onChange={goToPage} />

      {pendingDelete && (
        <ConfirmDialog
          title="Xóa hồ sơ"
          blockedReason={pendingDelete.status === "processing" ? "Hồ sơ đang xử lý — chờ xong rồi xóa." : null}
          message={<>Xóa vĩnh viễn hồ sơ <b>{pendingDelete.name}</b> (file PDF gốc + mọi bản cắt). Hành động này
            KHÔNG thể hoàn tác.</>}
          busy={deleteBusy}
          onConfirm={confirmDeleteGcn}
          onCancel={() => setPendingDelete(null)}
        />
      )}

      {smapPreview && (
        <Modal title={`File khớp ở kho nguồn · ${smapPreview.title}`}
          onClose={() => setSmapPreview(null)} wide>
          <GcnPdf gcnId={smapPreview.gcnId} smapIndex={smapPreview.smapIndex} page={smapPreview.page}
            onPageChange={(p) => setSmapPreview((s) => (s ? { ...s, page: p } : s))} />
        </Modal>
      )}
    </div>
  );
}
