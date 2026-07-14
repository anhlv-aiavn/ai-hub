import React, { useEffect, useMemo, useRef, useState } from "react";
import Icon from "./Icon.jsx";
import Modal from "./Modal.jsx";
import Pager from "./Pager.jsx";
import { listUsers, createUser, updateUser, deleteUser, listBatches } from "../api.js";
import { toastOk, toastErr } from "../toast.js";

const ROLE_LABEL = { admin: "Admin", operator: "Operator", viewer: "Viewer" };
const PAGE_SIZE = 10;

// Quản trị tài khoản (admin): tạo user, gán lô + vai trò, khóa/mở/đổi mật khẩu/xóa.
// 3 role: admin (config/secret/users, toàn hệ thống) · operator (import/hậu kiểm,
// khóa theo lô được gán) · viewer (tra cứu/xem, khóa theo lô được gán) — xem
// PLAN_.md §Phân quyền. Gán lô cũng quản lý được từ phía lô (AdminSettings.jsx,
// tab "Lô & phân quyền") — cả hai đều ghi vào `user.assigned_batch_ids`.
export default function Users({ me, onClose }) {
  const [rows, setRows] = useState([]);
  const [batches, setBatches] = useState([]);
  const [form, setForm] = useState({ username: "", password: "", role: "viewer", assignedBatchIds: [] });
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false); // chặn double-submit ngay lập tức (state busy cập nhật không đồng bộ)
  const [page, setPage] = useState(1);
  const [editingBatches, setEditingBatches] = useState(null); // username đang mở picker lô

  function batchName(id) { return batches.find((b) => b.batch_id === id)?.name || id; }

  const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  // Trang hiện tại có thể vượt quá sau khi xóa tài khoản/thêm bộ lọc → kẹp lại trong khoảng hợp lệ.
  const clampedPage = Math.min(page, totalPages);
  const pageRows = useMemo(
    () => rows.slice((clampedPage - 1) * PAGE_SIZE, clampedPage * PAGE_SIZE),
    [rows, clampedPage],
  );

  async function refresh() {
    try { const d = await listUsers(); setRows(d.users || []); } catch (e) { toastErr(e.message || e); }
  }
  useEffect(() => {
    refresh();
    listBatches(500).then((d) => setBatches(d.batches || [])).catch(() => {});
  }, []);
  // Trạng thái "đang hoạt động" đổi theo hành động của NGƯỜI KHÁC (họ đăng nhập/
  // thoát) — modal đang mở không tự biết, phải tự làm mới định kỳ mới thấy ngay
  // thay vì phải đóng/mở lại danh sách.
  useEffect(() => {
    const id = setInterval(refresh, 15000);
    return () => clearInterval(id);
  }, []);

  async function add() {
    if (busyRef.current) return; // đã có 1 lượt tạo đang chạy — bỏ qua các cú bấm dồn dập
    if (!form.username || form.password.length < 4) { toastErr("Tên đăng nhập / mật khẩu (≥4) chưa hợp lệ"); return; }
    busyRef.current = true;
    setBusy(true);
    try {
      await createUser({
        username: form.username.trim(), password: form.password, role: form.role,
        assigned_batch_ids: form.role !== "admin" ? form.assignedBatchIds : [],
      });
      toastOk("Đã tạo tài khoản");
      setForm({ username: "", password: "", role: "viewer", assignedBatchIds: [] });
      refresh();
    } catch (e) { toastErr(e.message || e); } finally { busyRef.current = false; setBusy(false); }
  }

  async function patch(username, body, okMsg) {
    try { await updateUser(username, body); toastOk(okMsg || "Đã cập nhật"); refresh(); }
    catch (e) { toastErr(e.message || e); }
  }
  function toggleFormBatch(id) {
    setForm((f) => {
      const has = f.assignedBatchIds.includes(id);
      return { ...f, assignedBatchIds: has ? f.assignedBatchIds.filter((x) => x !== id) : [...f.assignedBatchIds, id] };
    });
  }
  function toggleRowBatch(u, id) {
    const cur = u.assigned_batch_ids || [];
    const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
    patch(u.username, { assigned_batch_ids: next });
  }
  async function resetPw(username) {
    const pw = window.prompt(`Đặt mật khẩu mới cho "${username}" (≥4 ký tự):`);
    if (pw == null) return;
    if (pw.length < 4) { toastErr("Mật khẩu quá ngắn"); return; }
    patch(username, { password: pw }, "Đã đổi mật khẩu");
  }
  async function remove(username) {
    if (!window.confirm(`Xóa tài khoản "${username}"?`)) return;
    try { await deleteUser(username); toastOk("Đã xóa"); refresh(); }
    catch (e) { toastErr(e.message || e); }
  }

  return (
    <Modal title="Quản trị tài khoản" onClose={onClose} wide>
      <div className="users-panel">
      <div className="user-form">
        <div className="uf-field">
          <label className="field-label" htmlFor="uf-username">Tài khoản <span className="req">*</span></label>
          <input id="uf-username" className="text-input" placeholder="Tên đăng nhập" value={form.username}
            onChange={(e) => setForm({ ...form, username: e.target.value })} />
        </div>
        <div className="uf-field">
          <label className="field-label" htmlFor="uf-password">Mật khẩu <span className="req">*</span></label>
          <input id="uf-password" className="text-input" type="password" placeholder="≥ 4 ký tự" value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })} />
        </div>
        <div className="uf-field">
          <label className="field-label" htmlFor="uf-role">Vai trò <span className="req">*</span></label>
          <select id="uf-role" className="text-input" value={form.role}
            onChange={(e) => setForm({ ...form, role: e.target.value })}>
            <option value="viewer">Viewer</option>
            <option value="operator">Operator</option>
            <option value="admin">Admin</option>
          </select>
        </div>
        <div className="uf-field">
          <label className="field-label">Lô được gán</label>
          {form.role === "admin" ? (
            <span className="muted small">(toàn hệ thống)</span>
          ) : (
            <div className="batch-picker">
              {!batches.length && <span className="muted small">Chưa có lô nào — gán sau.</span>}
              {batches.map((b) => (
                <label key={b.batch_id} className="batch-picker-row">
                  <input type="checkbox" checked={form.assignedBatchIds.includes(b.batch_id)}
                    onChange={() => toggleFormBatch(b.batch_id)} />
                  {b.name}
                </label>
              ))}
            </div>
          )}
        </div>
        <div className="uf-field uf-submit">
          <label className="field-label" aria-hidden="true">&nbsp;</label>
          <button className="primary sm" disabled={busy} onClick={add}>
            <Icon name="plus" size={14} /> Tạo
          </button>
        </div>
      </div>

      <div className="et-scroll">
        <table className="et-grid">
          <thead>
            <tr><th>Tài khoản</th><th>Vai trò</th><th>Lô được gán</th><th>Trạng thái</th><th>Phiên</th><th>Thao tác</th></tr>
          </thead>
          <tbody>
            {pageRows.map((u) => {
              const isSelf = u.username === me?.username;
              const locked = u.online; // đang có phiên hoạt động → khóa thao tác nhạy cảm, tránh xung đột
              // Chính mình LUÔN "đang hoạt động" khi đang dùng bảng này — không áp
              // luật "đang hoạt động" lên nút đổi mật khẩu của chính mình, không thì
              // sẽ không bao giờ tự đổi được (khớp guard phía backend ở users.py).
              const resetPwLocked = locked && !isSelf;
              return (
              <tr key={u.username}>
                <td className="bt-name">{u.username}{u.username === me?.username && " (bạn)"}</td>
                <td>{ROLE_LABEL[u.role] || u.role}</td>
                <td>
                  {u.role === "admin" ? (
                    <span className="muted">toàn hệ thống</span>
                  ) : (
                    <div className="batch-cell">
                      <button type="button" className="ghost xs"
                        title={(u.assigned_batch_ids || []).map(batchName).join(", ") || "Chưa gán lô nào"}
                        onClick={() => setEditingBatches(editingBatches === u.username ? null : u.username)}>
                        {(u.assigned_batch_ids || []).length} lô
                      </button>
                      {editingBatches === u.username && (
                        <div className="batch-picker batch-picker-popover">
                          {!batches.length && <span className="muted small">Chưa có lô nào.</span>}
                          {batches.map((b) => (
                            <label key={b.batch_id} className="batch-picker-row">
                              <input type="checkbox" checked={(u.assigned_batch_ids || []).includes(b.batch_id)}
                                onChange={() => toggleRowBatch(u, b.batch_id)} />
                              {b.name}
                            </label>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </td>
                <td>
                  <span className={`badge ${u.active ? "rv-reviewed" : "st-error"}`}>
                    {u.active ? "Hoạt động" : "Đã khóa"}
                  </span>
                </td>
                <td>
                  <span className={`badge ${u.online ? "st-done" : "st-queued"}`}>
                    {u.online ? "Đang hoạt động" : "Ngoại tuyến"}
                  </span>
                </td>
                <td className="user-actions">
                  <button className="ghost xs" disabled={resetPwLocked} title={resetPwLocked ? "Đang hoạt động — buộc đăng xuất trước" : ""}
                    onClick={() => resetPw(u.username)}>Đổi mật khẩu</button>
                  {u.username !== me?.username
                    ? (
                      <button className="ghost xs" disabled={locked && u.active} title={locked && u.active ? "Đang hoạt động — buộc đăng xuất trước" : ""}
                        onClick={() => patch(u.username, { active: !u.active })}>
                        {u.active ? "Khóa" : "Mở"}
                      </button>
                    ) : <span className="ua-slot" />}
                  {u.username !== me?.username
                    ? <button className="ghost xs danger" disabled={locked} title={locked ? "Đang hoạt động — buộc đăng xuất trước" : ""}
                        onClick={() => remove(u.username)}>Xóa</button>
                    : <span className="ua-slot" />}
                </td>
              </tr>
              );
            })}
            {!rows.length && <tr><td colSpan={6} className="muted center">Chưa có tài khoản nào.</td></tr>}
          </tbody>
        </table>
      </div>
      <Pager page={clampedPage} totalPages={totalPages} total={rows.length} unit="tài khoản" onChange={setPage} />
      </div>
    </Modal>
  );
}
