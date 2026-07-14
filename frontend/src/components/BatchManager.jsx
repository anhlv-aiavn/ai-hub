import React, { useEffect, useState } from "react";
import Icon from "./Icon.jsx";
import ConfirmDialog from "./ConfirmDialog.jsx";
import {
  listBatches, deleteBatch, listUsers, getBatchUsers, assignBatchUser, unassignBatchUser,
} from "../api.js";
import { toastOk, toastErr } from "../toast.js";
import { STATUS_LABEL } from "./ExtractTable.jsx";

const UNSAFE_STATUS = new Set(["processing", "importing"]);

function fmtDate(v) {
  if (!v) return "";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return "";
  return `${d.toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric" })} `
    + d.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit", hour12: false });
}

function ProgressMini({ counts }) {
  const c = counts || {};
  const total = Object.values(c).reduce((a, b) => a + (b || 0), 0);
  // % đã XỬ LÝ XONG (không còn queued/processing) — khớp nghĩa với badge trạng
  // thái (batch.status chuyển "done" khi hết pending, bất kể kết quả từng file
  // là done/error/skip). Dùng done/total riêng sẽ ra 0% dù batch đã "XONG" nếu
  // các file kết thúc ở trạng thái khác done (lỗi/bỏ qua) — gây hiểu nhầm là lỗi.
  const pending = (c.queued || 0) + (c.processing || 0);
  const pct = total ? Math.round(((total - pending) / total) * 100) : 0;
  return (
    <div className="bm-prog" title={`${c.done || 0}/${total} xong · ${c.error || 0} lỗi · ${c.skip || 0} bỏ qua · ${pending} đang chờ/xử lý`}>
      <div className="bt-bar"><div className="bt-fill" style={{ width: `${pct}%` }} /></div>
      <span className="bt-pct">{pct}%</span>
    </div>
  );
}

// Trang quản trị lô (admin-only, tab chính trên nav — xem App.jsx): liệt kê mọi
// lô, xóa vĩnh viễn 1 lô (backend DELETE /v1/batches/{id}), gán/bỏ gán tài
// khoản được truy cập (đối xứng 2 chiều với Users.jsx — cùng ghi vào
// `user.assigned_batch_ids`).
export default function BatchManager() {
  const [batches, setBatches] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(null); // batch_id đang mở panel gán user
  const [assigned, setAssigned] = useState([]);
  const [allUsers, setAllUsers] = useState([]);
  const [addUsername, setAddUsername] = useState("");
  const [assignBusy, setAssignBusy] = useState(false);
  const [pendingDelete, setPendingDelete] = useState(null); // lô đang chờ xác nhận xóa
  const [deleteBusy, setDeleteBusy] = useState(false);

  async function refresh() {
    setLoading(true);
    try { setBatches((await listBatches(500)).batches || []); }
    catch (e) { toastErr(e.message || e); } finally { setLoading(false); }
  }
  useEffect(() => {
    refresh();
    listUsers().then((d) => setAllUsers(d.users || [])).catch(() => {});
  }, []);

  async function refreshAssigned(batchId) {
    try { setAssigned((await getBatchUsers(batchId)).users || []); }
    catch (e) { toastErr(e.message || e); }
  }
  function toggleExpand(b) {
    if (expanded === b.batch_id) { setExpanded(null); return; }
    setExpanded(b.batch_id);
    refreshAssigned(b.batch_id);
  }
  async function assign(batchId) {
    if (!addUsername) return;
    setAssignBusy(true);
    try {
      await assignBatchUser(batchId, addUsername);
      setAddUsername("");
      toastOk("Đã gán");
      refreshAssigned(batchId);
    } catch (e) { toastErr(e.message || e); } finally { setAssignBusy(false); }
  }
  async function unassign(batchId, username) {
    setAssignBusy(true);
    try { await unassignBatchUser(batchId, username); toastOk("Đã bỏ gán"); refreshAssigned(batchId); }
    catch (e) { toastErr(e.message || e); } finally { setAssignBusy(false); }
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    setDeleteBusy(true);
    try {
      await deleteBatch(pendingDelete.batch_id);
      toastOk(`Đã xóa lô "${pendingDelete.name}"`);
      if (expanded === pendingDelete.batch_id) setExpanded(null);
      setPendingDelete(null);
      refresh();
    } catch (e) { toastErr(e.message || e); } finally { setDeleteBusy(false); }
  }

  return (
    <div className="panel batch-manager">
      <h2>Quản lý lô</h2>
      <p className="muted">Xóa vĩnh viễn 1 lô (không phục hồi được) và gán/bỏ gán tài khoản được truy cập từng lô.</p>

      <div className="tbl-dense bm-tbl">
        <div className="file-row bm-row bm-head">
          <span>Tên lô</span><span>Ngày tạo</span><span>Số hồ sơ</span>
          <span>Trạng thái</span><span>Tiến độ</span><span />
        </div>
        {batches.map((b) => {
          const unsafe = UNSAFE_STATUS.has(b.status);
          const isOpen = expanded === b.batch_id;
          return (
            <div key={b.batch_id} className="bm-item">
              <div className="file-row bm-row">
                <span className="fr-name" title={b.name}>{b.name}</span>
                <span className="fr-meta">{fmtDate(b.created_at)}</span>
                <span>{b.file_count || 0}</span>
                <span><span className={`badge st-${b.status}`}>{STATUS_LABEL[b.status] || b.status}</span></span>
                <ProgressMini counts={b.counts} />
                <span className="bm-actions">
                  <button type="button" className="ghost xs" onClick={() => toggleExpand(b)}>
                    <Icon name={isOpen ? "chevronDown" : "chevronRight"} size={13} /> User
                  </button>
                  <button type="button" className="ghost xs danger" disabled={unsafe}
                    title={unsafe ? "Lô đang xử lý — chờ xong rồi xóa" : undefined}
                    onClick={() => setPendingDelete(b)}>
                    <Icon name="trash" size={13} /> Xóa
                  </button>
                </span>
              </div>

              {isOpen && (
                <div className="bm-assign-panel">
                  <label className="field-label">Tài khoản được truy cập ({assigned.length})</label>
                  <div className="taginput">
                    {assigned.map((a) => (
                      <span className="tag" key={a.username}>{a.username}
                        <button type="button" disabled={assignBusy} onClick={() => unassign(b.batch_id, a.username)} aria-label={`Bỏ ${a.username}`}>
                          <Icon name="x" size={11} />
                        </button>
                      </span>
                    ))}
                    {!assigned.length && <span className="muted small">Chưa có ai được gán lô này.</span>}
                  </div>
                  <div className="admin-tab-toolbar">
                    <select className="text-input" value={addUsername} onChange={(e) => setAddUsername(e.target.value)}>
                      <option value="">— Chọn tài khoản để gán —</option>
                      {allUsers.filter((u) => u.role !== "admin" && !assigned.some((a) => a.username === u.username))
                        .map((u) => <option key={u.username} value={u.username}>{u.username} ({u.role})</option>)}
                    </select>
                    <button className="primary sm" disabled={assignBusy || !addUsername} onClick={() => assign(b.batch_id)}>
                      <Icon name="plus" size={13} /> Gán
                    </button>
                  </div>
                </div>
              )}
            </div>
          );
        })}
        {!batches.length && !loading && <div className="muted center" style={{ padding: 16 }}>Chưa có lô nào.</div>}
      </div>

      {pendingDelete && (
        <ConfirmDialog
          title="Xóa lô"
          confirmText={pendingDelete.name}
          blockedReason={UNSAFE_STATUS.has(pendingDelete.status)
            ? "Lô đang xử lý — chờ xong rồi xóa." : null}
          message={<>Xóa vĩnh viễn lô <b>{pendingDelete.name}</b> cùng <b>{pendingDelete.file_count || 0} hồ sơ</b> bên
            trong (file PDF gốc + mọi bản cắt trên kho lưu trữ). Hành động này KHÔNG thể hoàn tác.</>}
          busy={deleteBusy}
          onConfirm={confirmDelete}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </div>
  );
}
