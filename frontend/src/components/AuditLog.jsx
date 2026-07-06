import React, { useEffect, useState } from "react";
import Icon from "./Icon.jsx";
import Modal from "./Modal.jsx";
import { getAuditLog } from "../api.js";
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
  ["gcn.view", "Xem hồ sơ"],
  ["gcn.download", "Tải hồ sơ"],
  ["export.create", "Tải xuất nền"],
];

function fmtRelative(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const diffMin = Math.round((Date.now() - d.getTime()) / 60000);
  if (diffMin < 1) return "vừa xong";
  if (diffMin < 60) return `${diffMin} phút trước`;
  const diffH = Math.round(diffMin / 60);
  if (diffH < 24) return `${diffH} giờ trước`;
  return d.toLocaleDateString("vi-VN");
}

export default function AuditLog({ onClose }) {
  const [items, setItems] = useState([]);
  const [cursor, setCursor] = useState(null);
  const [action, setAction] = useState("");
  const [actor, setActor] = useState("");
  const [open, setOpen] = useState(null);
  const [loading, setLoading] = useState(false);

  async function load(before) {
    setLoading(true);
    try {
      const d = await getAuditLog({ action: action || undefined, actor: actor || undefined, beforeId: before });
      setItems((prev) => (before ? [...prev, ...d.items] : d.items));
      setCursor(d.next_cursor);
    } catch (e) { toastErr(e.message || e); } finally { setLoading(false); }
  }
  useEffect(() => { load(null); }, [action, actor]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Modal title="Audit log" onClose={onClose} wide>
      <div className="admin-tab-body">
        <div className="row" style={{ marginBottom: 10 }}>
          <select className="text-input" style={{ width: "auto" }} value={action} onChange={(e) => setAction(e.target.value)}>
            {ACTIONS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
          </select>
          <input className="text-input" style={{ width: "auto", flex: "1 1 150px" }} placeholder="Lọc theo actor…"
            value={actor} onChange={(e) => setActor(e.target.value)} />
        </div>

        <div className="tbl-dense audit-tbl">
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

        {cursor && (
          <button type="button" className="ghost sm" disabled={loading} onClick={() => load(cursor)}>
            {loading ? "Đang tải…" : "Tải thêm"}
          </button>
        )}
      </div>
    </Modal>
  );
}
