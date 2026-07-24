import React, { useEffect, useMemo, useRef, useState } from "react";
import Icon from "./Icon.jsx";
import GcnPdf from "./GcnPdf.jsx";
import EditableTree, { setAt } from "./EditableTree.jsx";
import ConfirmDialog from "./ConfirmDialog.jsx";
import { STATUS_LABEL } from "./ExtractTable.jsx";
import {
  getGcn, putReview, downloadGcn, deleteGcn,
  claimReviewLock, heartbeatReviewLock, releaseReviewLock,
  friendlyError,
} from "../api.js";
import { toastOk, toastErr } from "../toast.js";
import { copyToClipboard } from "../clipboard.js";

const HEARTBEAT_MS = 90_000; // TTL khóa 300s ở server — bump giữa chừng cho an toàn
const AUTOSAVE_MS = 4_000; // debounce sau khi ngừng sửa — tránh mất việc nếu quên bấm Lưu

// Thứ tự cột ưu tiên khi hậu kiểm (các trường quan trọng lên trước, còn lại giữ sau).
const CHU_COLS = ["Loại đối tượng", "Tên chủ", "Loại giấy tờ", "Số giấy tờ", "Địa chỉ"];
const THUA_COLS = ["Số thứ tự thửa", "Số hiệu tờ bản đồ", "Diện tích", "Địa chỉ"];
const NHA_COLS = ["Loại tài sản gắn liền với đất", "Diện tích xây dựng", "Diện tích sàn",
  "Địa chỉ", "Kết cấu", "Số tầng"];

// Index trang 0-based → chuỗi 1-based gọn, gộp đoạn liên tiếp: [0,1,2,4] → "1–3, 5".
function pageRange(idx) {
  const ns = [...new Set((idx || []).filter((n) => Number.isInteger(n)).map((n) => n + 1))].sort((a, b) => a - b);
  if (!ns.length) return "";
  const parts = [];
  let s = ns[0], p = ns[0];
  for (let i = 1; i < ns.length; i++) {
    if (ns[i] === p + 1) p = ns[i];
    else { parts.push(s === p ? `${s}` : `${s}–${p}`); s = p = ns[i]; }
  }
  parts.push(s === p ? `${s}` : `${s}–${p}`);
  return parts.join(", ");
}

// Đối soát 1 GCN: trái = ảnh PDF, phải = các trường bóc ra (sửa được).
// Hậu kiểm ghi review.overrides + display_name (raw extractions giữ nguyên).
function fmtDupDate(v) {
  if (!v) return "";
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric" });
}

export default function Reconcile({ user, gcnId, onBack, onOpen }) {
  const canDelete = user?.role === "admin" || user?.role === "operator";
  const [doc, setDoc] = useState(null);
  const [work, setWork] = useState([]);          // bản làm việc của extractions
  const [overrides, setOverrides] = useState({}); // path → value đã sửa
  const [deleted, setDeleted] = useState([]);    // chỉ số bản ghi GCN đã xoá
  const [name, setName] = useState("");
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState("");
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [pdfOpen, setPdfOpen] = useState(true);
  const [lockedBy, setLockedBy] = useState(null); // {by, expires_at} nếu người KHÁC đang giữ
  const [haveLock, setHaveLock] = useState(false); // true nếu CHÍNH mình giữ khóa (được sửa)
  const [lockError, setLockError] = useState(false); // không xác định được trạng thái khóa (lỗi mạng/server) — fail-safe: coi như KHÔNG sửa được, không fail-open
  const haveLockRef = useRef(false); // cùng giá trị haveLock nhưng đọc "live" trong cleanup (tránh stale closure)
  const savedSnapshotRef = useRef(""); // {overrides,deleted,name} đã lưu gần nhất — so sánh để biết còn gì chưa lưu

  function applyDoc(d) {
    const ov = (d.review && d.review.overrides) || {};
    // Áp overrides ĐÃ LƯU lên bản làm việc để mở lại thấy đúng giá trị đã sửa.
    const w = structuredClone(d.extractions || []);
    for (const [path, val] of Object.entries(ov)) {
      try { setAt(w, path, val); } catch { /* path lệch → bỏ qua */ }
    }
    const dl = (d.review && d.review.deleted) || [];
    const nm = (d.review && d.review.display_name) || "";
    setDoc(d);
    setWork(w);
    setOverrides(ov);
    setDeleted(dl);
    setName(nm);  // để trống → tên tệp tự theo SPH
    savedSnapshotRef.current = JSON.stringify({ overrides: ov, deleted: dl, name: nm });
  }

  useEffect(() => { haveLockRef.current = haveLock; }, [haveLock]);

  useEffect(() => {
    if (!gcnId) return;
    let live = true;
    setHaveLock(false); setLockedBy(null); setLockError(false); setPage(1);
    getGcn(gcnId).then((d) => {
      if (!live) return;
      applyDoc(d);
      return claimReviewLock(gcnId).then(() => { if (live) setHaveLock(true); });
    }).catch((e) => {
      if (!live) return;
      if (e.status === 409 && e.detail?.locked_by) setLockedBy(e.detail);
      // Không xác định được ai đang giữ khóa (lỗi mạng/500 khác) — vẫn phải CHẶN
      // sửa (fail-safe), không được coi im lặng = được sửa như trước (bug 2 người
      // cùng hậu kiểm 1 hồ sơ, người bấm sau vẫn sửa được do lỗi 500 bị bỏ qua).
      else { setLockError(true); toastErr(e.message || e); }
    });
    return () => {
      live = false;
      // haveLockRef (không phải biến haveLock đóng gói lúc effect chạy) để thấy
      // đúng trạng thái MỚI NHẤT — claim thường resolve SAU khi effect này chạy.
      if (haveLockRef.current) releaseReviewLock(gcnId).catch(() => {});
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gcnId]);

  // Heartbeat khi đang giữ khóa — không để hết hạn giữa chừng thao tác dài.
  useEffect(() => {
    if (!haveLock || !gcnId) return;
    const id = setInterval(() => { heartbeatReviewLock(gcnId).catch(() => {}); }, HEARTBEAT_MS);
    return () => clearInterval(id);
  }, [haveLock, gcnId]);

  // Autosave: debounce sau khi ngừng sửa — không đợi người dùng nhớ bấm Lưu, chỉ
  // lưu overrides/tên (KHÔNG đổi review.status, giống "Lưu" thủ công).
  useEffect(() => {
    if (!haveLock || lockError || busy) return;
    const cur = JSON.stringify({ overrides, deleted, name });
    if (cur === savedSnapshotRef.current) return;
    const t = setTimeout(() => { save(undefined, { auto: true }); }, AUTOSAVE_MS);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [overrides, deleted, name, haveLock, lockError, busy]);

  function onLeaf(path, val) {
    setWork((prev) => {
      const clone = structuredClone(prev);
      setAt(clone, path, val);
      return clone;
    });
    setOverrides((o) => ({ ...o, [path]: val }));
  }

  // 409 (version lệch) → người khác vừa lưu trong lúc mình đang sửa. KHÔNG ghi
  // đè mù: tải lại bản mới nhất, báo rõ để người dùng tự áp lại thay đổi của họ.
  async function reloadAfterConflict() {
    toastErr("Người khác vừa sửa hồ sơ này — đã tải lại bản mới nhất");
    try { applyDoc(await getGcn(gcnId)); } catch { /* giữ bản cũ nếu tải lại cũng lỗi */ }
  }

  async function save(status, { auto } = {}) {
    // Bấm lại nút đang được chọn → quay về "chưa kiểm" thay vì không đổi gì.
    const curStatus = doc?.review?.status || "unreviewed";
    const effStatus = status && status === curStatus ? "unreviewed" : status;
    setBusy(effStatus || "save");
    try {
      const res = await putReview(gcnId, {
        display_name: name.trim() || null,
        overrides,
        deleted,
        status: effStatus || undefined,
        version: doc?.review?.version ?? 0,
      });
      // Cập nhật version cục bộ ngay — thiếu bước này thì lần LƯU KẾ TIẾP trong
      // cùng phiên vẫn gửi version cũ → server từ chối nhầm dù chính mình vừa lưu.
      setDoc((prev) => (prev ? { ...prev, review: res.review } : prev));
      savedSnapshotRef.current = JSON.stringify({ overrides, deleted, name });
      // Autosave vẫn báo (chỉ đổi message) — trước đây im lặng hoàn toàn khiến
      // người dùng không biết nội dung có được lưu hay chưa khi rời ô nhập.
      toastOk(auto ? "Đã tự động lưu"
        : effStatus === "reviewed" ? "Đã duyệt"
        : effStatus === "needs_review" ? "Đã đánh dấu không duyệt"
        : status ? "Đã bỏ đánh dấu, chuyển về chưa kiểm" : "Đã lưu");
    } catch (e) {
      if (e.status === 409) await reloadAfterConflict();
      else toastErr(e.message || e);
    } finally { setBusy(""); }
  }

  async function removeGcn(ri) {
    if (!window.confirm("Xoá giấy chứng nhận này khỏi hồ sơ? (raw vẫn được giữ để truy lại)")) return;
    const nd = [...new Set([...deleted, ri])].sort((a, b) => a - b);
    setBusy("del");
    try {
      const res = await putReview(gcnId, {
        display_name: name.trim() || null, overrides, deleted: nd,
        version: doc?.review?.version ?? 0,
      });
      setDeleted(nd);
      setDoc((prev) => (prev ? { ...prev, review: res.review } : prev));
      savedSnapshotRef.current = JSON.stringify({ overrides, deleted: nd, name });
      toastOk("Đã xoá giấy chứng nhận");
    } catch (e) {
      if (e.status === 409) await reloadAfterConflict();
      else toastErr(e.message || e);
    } finally { setBusy(""); }
  }

  async function confirmDeleteDoc() {
    setDeleteBusy(true);
    try {
      await deleteGcn(gcnId);
      toastOk("Đã xóa hồ sơ");
      onBack();
    } catch (e) { toastErr(e.message || e); setDeleteBusy(false); }
  }

  const entries = useMemo(() => {
    const out = [];
    (work || []).forEach((rec, ri) => {
      if (deleted.includes(ri)) return;
      const list = rec?.result?.["Đăng ký"];
      if (Array.isArray(list)) list.forEach((entry, ei) => out.push({ ri, ei, entry, rec }));
    });
    return out;
  }, [work, deleted]);

  // Chủ cuối theo (rec_index, entry_index) — tra nhanh khi render từng giấy.
  const ccMap = useMemo(() => {
    const m = {};
    (doc?.chu_cuoi || []).forEach((cc) => { m[`${cc.rec_index}-${cc.entry_index}`] = cc; });
    return m;
  }, [doc]);

  // Render một khối của entry. Chủ sử dụng: đảo cột ưu tiên. Thửa đất: tách từng
  // thửa (trường chính + bảng Mục đích riêng). Còn lại: cây mặc định.
  function renderBlock(block, val, ri, ei) {
    const base = `[${ri}].result.Đăng ký[${ei}]`;
    if (block === "Chủ sử dụng" && Array.isArray(val)) {
      return <EditableTree value={val} path={`${base}.${block}`} onLeaf={onLeaf} colsOrder={CHU_COLS} />;
    }
    if (block === "Thông tin nhà ở" && Array.isArray(val)) {
      return <EditableTree value={val} path={`${base}.${block}`} onLeaf={onLeaf} colsOrder={NHA_COLS} />;
    }
    if (block === "Thửa đất" && Array.isArray(val)) {
      return (
        <div className="rc-thuas">
          {val.map((thua, ti) => (
            <div className="rc-thua" key={ti}>
              <div className="rc-thua-head">Thửa đất {ti + 1}</div>
              <EditableTree value={thua} path={`${base}.${block}[${ti}]`} onLeaf={onLeaf}
                skipKeys={["Mục đích sử dụng"]} colsOrder={THUA_COLS} />
              {Array.isArray(thua?.["Mục đích sử dụng"]) && thua["Mục đích sử dụng"].length > 0 && (
                <div className="rc-sub">
                  <div className="rc-sub-name">Mục đích sử dụng</div>
                  <EditableTree value={thua["Mục đích sử dụng"]}
                    path={`${base}.${block}[${ti}].Mục đích sử dụng`} onLeaf={onLeaf} />
                </div>
              )}
            </div>
          ))}
        </div>
      );
    }
    return <EditableTree value={val} path={`${base}.${block}`} onLeaf={onLeaf} />;
  }

  if (!doc) return <div className="panel muted">Đang tải…</div>;
  const dirty = Object.keys(overrides).length > 0 || name !== ((doc.review && doc.review.display_name) || "");
  const readOnly = (!!lockedBy && !haveLock) || lockError;
  const reviewStatus = doc.review?.status || "unreviewed";
  // Tên hồ sơ ĐANG XEM, hiện rõ bằng nhãn riêng — không dựa vào placeholder của
  // ô đổi tên (placeholder rỗng khi chưa đặt tên, không cho biết tên hiệu lực
  // thực tế đang là gì). Lấy theo tên đã LƯU (không phải bản đang gõ dở).
  const currentName = (doc.review && doc.review.display_name) || doc.filename || gcnId;
  // Tên hồ sơ giờ có thể đã kèm sẵn đuôi file gốc (server tự thêm, xem
  // routes/gcn.py put_review) — bỏ đuôi đó trước khi ghép ".zip" khi tải về,
  // tránh tên file kiểu "...pdf.zip" nhìn như lỗi nhân đôi đuôi.
  const zipBase = name || currentName;
  const zipBaseName = zipBase.toLowerCase().endsWith(".pdf") ? zipBase.slice(0, -4) : zipBase;

  return (
    <div className="panel reconcile">
      <div className="rc-toolbar">
        <button className="ghost sm" onClick={onBack}><Icon name="chevronLeft" size={14} /> Kết quả trích xuất</button>
        <span className={`badge st-${doc.status}`}>{STATUS_LABEL[doc.status] || doc.status}</span>
        <span className="rc-current-name" title="Hồ sơ đang xem">{currentName}</span>
        <button type="button" className="ghost xs" title={`ID: ${gcnId}`}
          onClick={() => copyToClipboard(gcnId).then(() => toastOk("Đã copy ID hồ sơ")).catch(() => toastErr("Trình duyệt không hỗ trợ copy"))}>
          <Icon name="copy" size={13} /> ID
        </button>
        <input className="rc-name" placeholder="Đặt tên hồ sơ…" value={name} disabled={readOnly}
          onChange={(e) => setName(e.target.value)}
          title={readOnly
            ? (lockedBy ? `Đang được ${lockedBy.locked_by} hậu kiểm — không sửa được lúc này` : "Không xác định được trạng thái khóa — không sửa được lúc này")
            : "Tên hiển thị / tên file khi tải"} />
        <span className="tb-gap" />
        <button className="ghost sm" onClick={() => setPdfOpen((v) => !v)}>
          <Icon name="image" size={14} /> {pdfOpen ? "Ẩn PDF" : "Hiện PDF"}
        </button>
        {!readOnly && (
          <>
            <button className="ghost sm" disabled={busy} onClick={() => save()}>
              {busy === "save" ? "…" : "Lưu"}
            </button>
            <button className={`ghost sm danger${reviewStatus === "needs_review" ? " active" : ""}`}
              disabled={busy} onClick={() => save("needs_review")}
              title="Lưu và đánh dấu hồ sơ này KHÔNG đạt — cần người khác xử lý lại (bấm lại để bỏ đánh dấu)">
              <Icon name="ban" size={14} /> Không duyệt
            </button>
            <button className={`ghost sm${reviewStatus === "reviewed" ? " active" : ""}`}
              disabled={busy} onClick={() => save("reviewed")}
              title="Lưu và đánh dấu hồ sơ này đã duyệt (bấm lại để bỏ đánh dấu)">
              <Icon name="check" size={14} /> Duyệt
            </button>
          </>
        )}
        <button className="primary sm" onClick={() => downloadGcn(gcnId, `${zipBaseName}.zip`)}>
          <Icon name="download" size={14} /> Tải hồ sơ
        </button>
        {canDelete && !readOnly && (
          <button className="danger sm" onClick={() => setConfirmDeleteOpen(true)}>
            <Icon name="trash" size={14} /> Xóa hồ sơ
          </button>
        )}
      </div>

      {readOnly && (
        <div className="auth-err rc-lock-banner">
          <Icon name="ban" size={14} />
          {lockedBy
            ? <>Hồ sơ đang được <b>{lockedBy.locked_by}</b> hậu kiểm — chỉ xem, không sửa được lúc này.</>
            : "Không xác định được ai đang hậu kiểm hồ sơ này (lỗi kết nối) — chỉ xem, tải lại trang để thử lại."}
        </div>
      )}

      {doc.dup_suspect && (doc.dup_candidates || []).length > 0 && (() => {
        // Hiện ĐẦY ĐỦ cả nhóm, KỂ CẢ hồ sơ đang xem — bấm sang hồ sơ khác trong
        // nhóm vẫn thấy đúng 1 danh sách y hệt (chỉ khác mục nào đánh dấu "đang
        // xem"), tránh cảm giác "danh sách bị đổi" khi hồ sơ tự nó bị loại khỏi
        // danh sách nghi trùng của chính nó (dup_candidates vốn chỉ chứa "hồ sơ
        // KHÁC", không có chính nó).
        const group = [
          { gcn_id: doc.gcn_id, filename: doc.filename, display_name: doc.review?.display_name,
            created_at: doc.created_at, status: doc.status },
          ...doc.dup_candidates,
        ].sort((a, b) => new Date(a.created_at || 0) - new Date(b.created_at || 0));
        return (
          <div className="admin-warn rc-dup-banner">
            <Icon name="alertTriangle" size={16} />
            <div>
              <b>Nghi trùng nội dung</b> — {group.length} hồ sơ cùng Số phát hành:
              <div className="rc-dup-list">
                {group.map((c) => {
                  const isCurrent = c.gcn_id === gcnId;
                  return (
                    <button type="button" key={c.gcn_id} className={`ghost xs${isCurrent ? " rc-dup-current" : ""}`}
                      disabled={isCurrent} onClick={() => onOpen?.(c.gcn_id)}>
                      {isCurrent && <Icon name="checkCircle" size={12} />}
                      {c.display_name || c.filename || c.gcn_id}
                      {" · "}{isCurrent ? "Đang xem" : `${fmtDupDate(c.created_at)} · ${STATUS_LABEL[c.status] || c.status || "?"}`}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        );
      })()}

      <div className={`rc-body ${pdfOpen ? "with-pdf" : "no-pdf"}`}>
        {pdfOpen && (
          <div className="rc-left">
            <GcnPdf gcnId={gcnId} page={page} onPageChange={setPage} />
          </div>
        )}
        <div className={`rc-right ${readOnly ? "rc-readonly" : ""}`}>
          {doc.error && <div className="rc-err" title={doc.error}>Lỗi: {friendlyError(doc.error)}</div>}
          {!entries.length && <div className="muted">Không có dữ liệu bóc tách.</div>}
          {entries.map(({ ri, ei, entry, rec }) => {
            const range = pageRange(rec?.page_indices);
            const firstPage = Math.min(...(rec?.page_indices || [0]).filter(Number.isInteger)) + 1;
            return (
            <div className="rc-entry" key={`${ri}-${ei}`}>
              <div className="rc-entry-head">
                <Icon name="fileText" size={15} />
                Số phát hành: <b>{entry?.["Giấy chứng nhận"]?.["Số phát hành"] || "—"}</b>
                {(() => {
                  const cc = ccMap[`${ri}-${ei}`];
                  if (!cc) return null;
                  if (cc.canh_bao) return (
                    <span className="rc-cc rc-cc-warn" title="Có chuyển nhượng nhưng chưa rõ chủ — cần xác minh">
                      <Icon name="alertTriangle" size={13} /> Chủ cuối: chưa rõ (có chuyển nhượng)
                    </span>
                  );
                  const names = (cc.chu || []).map((c) => c["Tên chủ"]).filter(Boolean).join(", ");
                  if (!names) return null;
                  return (
                    <span className="rc-cc" title={cc.nguon === "bien_dong" ? `Suy từ biến động${cc.thoi_gian ? " " + cc.thoi_gian : ""}` : "Chủ trên giấy gốc"}>
                      Chủ cuối: <b>{names}</b>
                      {cc.nguon === "bien_dong" && <span className="rc-cc-src"> (biến động)</span>}
                    </span>
                  );
                })()}
                {range && (
                  <button type="button" className="rc-range" title="Tới trang gốc đầu của giấy này"
                    onClick={() => { setPdfOpen(true); setPage(firstPage); }}>
                    Trang gốc {range}
                  </button>
                )}
                {!readOnly && (
                  <button type="button" className="rc-del" disabled={busy} title="Xoá giấy chứng nhận này"
                    onClick={() => removeGcn(ri)}>
                    <Icon name="trash" size={14} />
                  </button>
                )}
              </div>
              {Object.entries(entry).map(([block, val]) => (
                <div className="rc-block" key={block}>
                  <div className="rc-block-name">{block}</div>
                  {renderBlock(block, val, ri, ei)}
                </div>
              ))}
            </div>
            );
          })}
        </div>
      </div>

      {confirmDeleteOpen && (
        <ConfirmDialog
          title="Xóa hồ sơ"
          message={<>Xóa vĩnh viễn hồ sơ <b>{currentName}</b> (file PDF gốc + mọi bản cắt). Hành động này
            KHÔNG thể hoàn tác.</>}
          busy={deleteBusy}
          onConfirm={confirmDeleteDoc}
          onCancel={() => setConfirmDeleteOpen(false)}
        />
      )}
    </div>
  );
}
