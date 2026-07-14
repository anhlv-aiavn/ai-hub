import React, { useEffect, useState } from "react";
import Icon from "./Icon.jsx";
import { getAuditLog, getAuditLogActors } from "../api.js";
import { toastErr } from "../toast.js";

// Mọi hành động (cấu hình LẪN nghiệp vụ) đều nằm trong 1 collection `audit_log`
// — xem app/audit.py::AuditAction. Không còn tách "Audit truy cập" riêng.
const ACTIONS = [
  ["", "Mọi hành động"],
  ["site_config.update", "Sửa cấu hình tổ chức"],
  ["s3_connection.create", "Thêm S3 connection"],
  ["s3_connection.update", "Sửa S3 connection"],
  ["s3_connection.delete", "Xóa S3 connection"],
  ["s3_connection.test", "Test S3 connection"],
  ["gcn.upload", "Upload file để trích xuất"],
  ["gcn.import_minio", "Chọn file MinIO để trích xuất"],
  ["gcn.edit", "Sửa hồ sơ (hậu kiểm)"],
  ["gcn.rows_delete", "Xóa dòng trong hồ sơ"],
  ["gcn.delete", "Xóa hồ sơ"],
  ["gcn.view", "Xem hồ sơ"],
  ["gcn.download", "Tải hồ sơ"],
  ["batch.delete", "Xóa lô"],
  ["export.create", "Tải xuất nền"],
];

const TARGET_HELP = "Dán ID để lọc theo 1 đối tượng cụ thể: ID hồ sơ (nút Copy ID ở màn "
  + "chi tiết hồ sơ) hoặc ID S3 connection (nút Copy ID ở Cấu hình hệ thống → S3 nguồn/đích).";

function fmtRelative(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const diffMin = Math.round((Date.now() - d.getTime()) / 60000);
  if (diffMin < 1) return "vừa xong";
  if (diffMin < 60) return `${diffMin} phút trước`;
  const diffH = Math.round(diffMin / 60);
  if (diffH < 24) return `${diffH} giờ trước`;
  // > 24h: ngày + giờ cố định (chỉ có ngày thì không biết bản ghi xảy ra lúc nào trong ngày).
  return `${d.toLocaleDateString("vi-VN")} ${d.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit", hour12: false })}`;
}

export default function AuditLog() {
  // Phân trang theo TRANG (không phải "tải thêm" accumulate) — nhưng vẫn dùng
  // cursor `before_id` sẵn có ở backend (không đổi sang skip/limit, xem PLAN_
  // audit_log_id_visibility.md §Phản biện: audit_log append-only không TTL,
  // skip sâu sẽ ngày càng chậm). pages[i] = trang đã tải, cache lại để "Trang
  // trước" không phải gọi lại API.
  const [pages, setPages] = useState([]);
  const [pageIndex, setPageIndex] = useState(0);
  const [action, setAction] = useState("");
  const [actor, setActor] = useState("");
  const [actors, setActors] = useState([]);
  const [target, setTarget] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [open, setOpen] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    getAuditLogActors().then((d) => setActors(d.actors || [])).catch(() => {});
  }, []);

  async function loadPage(beforeId, targetIndex) {
    setLoading(true);
    try {
      const d = await getAuditLog({
        action: action || undefined, actor: actor || undefined,
        target: target || undefined,
        // "Đến ngày" phải kèm cuối ngày — backend so $lte chính xác theo giờ
        // gửi lên, gửi nguyên ngày sẽ hiểu 00:00:00 và mất cả ngày đó.
        from: dateFrom || undefined, to: dateTo ? `${dateTo}T23:59:59` : undefined,
        beforeId,
      });
      setPages((prev) => {
        const next = prev.slice(0, targetIndex);
        next[targetIndex] = { items: d.items, nextCursor: d.next_cursor };
        return next;
      });
      setPageIndex(targetIndex);
      setOpen(null);
    } catch (e) { toastErr(e.message || e); } finally { setLoading(false); }
  }
  // Đổi bất kỳ bộ lọc nào → về trang đầu, bỏ cache các trang cũ (không còn khớp filter mới).
  useEffect(() => { loadPage(null, 0); }, [action, actor, target, dateFrom, dateTo]); // eslint-disable-line react-hooks/exhaustive-deps

  const current = pages[pageIndex];
  const items = current?.items || [];

  function prevPage() {
    if (pageIndex === 0) return;
    setPageIndex(pageIndex - 1);
    setOpen(null);
  }
  function nextPage() {
    if (!current?.nextCursor) return;
    if (pages[pageIndex + 1]) { setPageIndex(pageIndex + 1); setOpen(null); return; }
    loadPage(current.nextCursor, pageIndex + 1);
  }

  return (
    <div className="panel audit-log-view">
      <h2>Audit log</h2>
      <div className="admin-tab-body">
        <div className="audit-filters">
          <select className="text-input" value={action} onChange={(e) => setAction(e.target.value)}>
            {ACTIONS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
          </select>
          <select className="text-input" value={actor} onChange={(e) => setActor(e.target.value)}>
            <option value="">Mọi tài khoản</option>
            {actors.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
          <input className="text-input target-input" placeholder="ID hồ sơ / connection…" title={TARGET_HELP}
            value={target} onChange={(e) => setTarget(e.target.value)} />
          <div className="date-range-field" title="Khoảng thời gian: từ ngày – đến ngày">
            <input className="text-input" type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
            <span className="dr-sep">–</span>
            <input className="text-input" type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
          </div>
        </div>

        <div className="tbl-dense audit-tbl">
          <div className="file-row audit-row audit-head">
            <span>Thời gian</span>
            <span>Tài khoản</span>
            <span>Hành động</span>
            <span>ID đối tượng</span>
            <span />
          </div>
          {items.map((r) => (
            <div key={r.id} className="audit-item">
              <div className="file-row audit-row" role="button" tabIndex={0}
                onClick={() => setOpen(open === r.id ? null : r.id)}>
                <span className="fr-meta mono" title={r.at}>{fmtRelative(r.at)}</span>
                <span className="fr-name">{r.actor}</span>
                <span className="badge">{r.action}</span>
                <span className="fr-meta mono">{r.target || "—"}</span>
                <Icon name={open === r.id ? "chevronDown" : "chevronRight"} size={14} />
              </div>
              {open === r.id && (
                <pre className="audit-detail">{JSON.stringify(r.detail, null, 2)}</pre>
              )}
            </div>
          ))}
          {!items.length && !loading && <div className="muted center" style={{ padding: 16 }}>Chưa có bản ghi.</div>}
        </div>

        <div className="audit-pager">
          <button type="button" className="pager-btn" disabled={pageIndex === 0 || loading} onClick={prevPage}>
            <Icon name="chevronLeft" size={14} /> Trang trước
          </button>
          <span className="pager-info">{loading ? "Đang tải…" : `Trang ${pageIndex + 1}`}</span>
          <button type="button" className="pager-btn" disabled={!current?.nextCursor || loading} onClick={nextPage}>
            Trang sau <Icon name="chevronRight" size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}
