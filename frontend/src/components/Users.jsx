import React, { useEffect, useState } from "react";
import Icon from "./Icon.jsx";
import { listUsers, createUser, updateUser, deleteUser, getBranches } from "../api.js";
import { toastOk, toastErr } from "../toast.js";

// Quản trị tài khoản (admin): tạo user, gán chi nhánh + vai trò, khóa/mở/đổi mật khẩu/xóa.
export default function Users({ me, onClose }) {
  const [rows, setRows] = useState([]);
  const [branches, setBranches] = useState([]);
  const [form, setForm] = useState({ username: "", password: "", role: "user", branch: "" });
  const [busy, setBusy] = useState(false);

  async function refresh() {
    try { const d = await listUsers(); setRows(d.users || []); } catch (e) { toastErr(e.message || e); }
  }
  useEffect(() => {
    refresh();
    getBranches().then((d) => setBranches(d.branches || [])).catch(() => {});
  }, []);

  async function add() {
    if (!form.username || form.password.length < 4) { toastErr("Tên đăng nhập / mật khẩu (≥4) chưa hợp lệ"); return; }
    if (form.role === "user" && !form.branch) { toastErr("User thường phải chọn chi nhánh"); return; }
    setBusy(true);
    try {
      await createUser({
        username: form.username.trim(), password: form.password, role: form.role,
        branch: form.role === "user" ? form.branch : null,
      });
      toastOk("Đã tạo tài khoản");
      setForm({ username: "", password: "", role: "user", branch: "" });
      refresh();
    } catch (e) { toastErr(e.message || e); } finally { setBusy(false); }
  }

  async function patch(username, body, okMsg) {
    try { await updateUser(username, body); toastOk(okMsg || "Đã cập nhật"); refresh(); }
    catch (e) { toastErr(e.message || e); }
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
    <div className="panel users-panel">
      <div className="export-head">
        <h3>Quản trị tài khoản</h3>
        <button className="icon-btn" onClick={onClose} aria-label="Đóng"><Icon name="x" size={16} /></button>
      </div>

      <div className="user-form">
        <input className="text-input" placeholder="Tên đăng nhập" value={form.username}
          onChange={(e) => setForm({ ...form, username: e.target.value })} />
        <input className="text-input" type="password" placeholder="Mật khẩu (≥4)" value={form.password}
          onChange={(e) => setForm({ ...form, password: e.target.value })} />
        <select className="text-input" value={form.role}
          onChange={(e) => setForm({ ...form, role: e.target.value })}>
          <option value="user">User</option>
          <option value="admin">Admin</option>
        </select>
        <select className="text-input" value={form.branch} disabled={form.role === "admin"}
          onChange={(e) => setForm({ ...form, branch: e.target.value })}>
          <option value="">{form.role === "admin" ? "(toàn hệ thống)" : "— Chọn chi nhánh —"}</option>
          {branches.map((b) => <option key={b} value={b}>{b}</option>)}
        </select>
        <button className="primary sm" disabled={busy} onClick={add}>
          <Icon name="plus" size={14} /> Tạo
        </button>
      </div>

      <div className="et-scroll">
        <table className="et-grid">
          <thead>
            <tr><th>Tài khoản</th><th>Vai trò</th><th>Chi nhánh</th><th>Trạng thái</th><th>Thao tác</th></tr>
          </thead>
          <tbody>
            {rows.map((u) => (
              <tr key={u.username}>
                <td className="bt-name">{u.username}{u.username === me?.username && " (bạn)"}</td>
                <td>{u.role === "admin" ? "Admin" : "User"}</td>
                <td>{u.branch || "—"}</td>
                <td>
                  <span className={`badge ${u.active ? "rv-reviewed" : "st-error"}`}>
                    {u.active ? "Hoạt động" : "Đã khóa"}
                  </span>
                </td>
                <td className="user-actions">
                  <button className="ghost xs" onClick={() => resetPw(u.username)}>Đổi mật khẩu</button>
                  {u.username !== me?.username && (
                    <>
                      <button className="ghost xs" onClick={() => patch(u.username, { active: !u.active })}>
                        {u.active ? "Khóa" : "Mở"}
                      </button>
                      <button className="ghost xs danger" onClick={() => remove(u.username)}>Xóa</button>
                    </>
                  )}
                </td>
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={5} className="muted center">Chưa có tài khoản nào.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
