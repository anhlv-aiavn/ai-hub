import React, { useState } from "react";
import Modal from "./Modal.jsx";
import { changeMyPassword } from "../api.js";
import { toastOk, toastErr } from "../toast.js";

// Tự đổi mật khẩu của chính mình — mọi vai trò (viewer/operator/admin), yêu
// cầu đúng mật khẩu hiện tại. Khác bảng Quản trị tài khoản (chỉ admin, đổi
// được cho NGƯỜI KHÁC không cần biết mật khẩu cũ của họ).
export default function ChangePasswordModal({ onClose }) {
  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    if (busy) return;
    if (newPw.length < 4) { toastErr("Mật khẩu mới phải từ 4 ký tự"); return; }
    if (newPw !== confirmPw) { toastErr("Xác nhận mật khẩu mới không khớp"); return; }
    setBusy(true);
    try {
      await changeMyPassword(oldPw, newPw);
      toastOk("Đã đổi mật khẩu");
      onClose?.();
    } catch (e2) { toastErr(e2.message || e2); } finally { setBusy(false); }
  }

  return (
    <Modal title="Đổi mật khẩu của tôi" onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <label className="field-label" htmlFor="cp-old">Mật khẩu hiện tại</label>
        <input id="cp-old" className="text-input" type="password" autoFocus autoComplete="current-password"
          value={oldPw} disabled={busy} onChange={(e) => setOldPw(e.target.value)} />

        <label className="field-label" htmlFor="cp-new">Mật khẩu mới (≥ 4 ký tự)</label>
        <input id="cp-new" className="text-input" type="password" autoComplete="new-password"
          value={newPw} disabled={busy} onChange={(e) => setNewPw(e.target.value)} />

        <label className="field-label" htmlFor="cp-confirm">Nhập lại mật khẩu mới</label>
        <input id="cp-confirm" className="text-input" type="password" autoComplete="new-password"
          value={confirmPw} disabled={busy} onChange={(e) => setConfirmPw(e.target.value)} />

        <div className="cb-foot" style={{ marginTop: 14 }}>
          <span />
          <button className="primary sm" disabled={busy || !oldPw || !newPw || !confirmPw}>
            {busy ? "Đang lưu…" : "Đổi mật khẩu"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
