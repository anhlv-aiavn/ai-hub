import React, { useState } from "react";
import Icon from "./Icon.jsx";
import { login } from "../api.js";
import { toastErr } from "../toast.js";

// Màn đăng nhập — gate toàn bộ app khi chưa có token hợp lệ.
export default function Login({ onLogin }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    if (!username || !password) return;
    setBusy(true);
    try {
      const user = await login(username.trim(), password);
      onLogin?.(user);
    } catch (err) { toastErr(err.message || err); } finally { setBusy(false); }
  }

  return (
    <div className="login-wrap">
      <form className="login-card panel" onSubmit={submit}>
        <div className="login-brand">
          <img className="brand-logo" src="/logo-sotnmt.png" alt="Sở TN&MT Hà Nội" /> AI-HUB
        </div>
        <p className="login-org">Văn phòng Đăng ký đất đai thành phố Hà Nội</p>
        <p className="muted login-sub">Số hóa & đối soát Giấy Chứng Nhận Quyền sử dụng đất</p>

        <label className="field-label" htmlFor="lg-u">Tài khoản</label>
        <input id="lg-u" className="text-input" value={username} autoFocus autoComplete="username"
          onChange={(e) => setUsername(e.target.value)} />

        <label className="field-label" htmlFor="lg-p">Mật khẩu</label>
        <input id="lg-p" className="text-input" type="password" value={password} autoComplete="current-password"
          onChange={(e) => setPassword(e.target.value)} />

        <button className="primary login-btn" disabled={busy || !username || !password}>
          <Icon name="check" size={15} /> {busy ? "Đang đăng nhập…" : "Đăng nhập"}
        </button>
      </form>
    </div>
  );
}
