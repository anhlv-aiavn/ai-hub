import React, { useState } from "react";
import Icon from "./Icon.jsx";
import { login } from "../api.js";

// Màn đăng nhập — card ngang 2 panel (brand · form), gate toàn bộ app khi chưa có token.
export default function Login({ onLogin }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const year = new Date().getFullYear();

  async function submit(e) {
    e.preventDefault();
    if (!username || !password || busy) return;
    setBusy(true);
    setError("");
    try {
      const user = await login(username.trim(), password);
      onLogin?.(user);
    } catch (err) {
      setError(err?.message || "Không đăng nhập được. Thử lại.");
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <main className="auth">
        <aside className="auth-brand">
          <div className="ab-top">
            <img className="ab-logo" src="/logo-sotnmt.png" alt="Sở TN&MT Hà Nội" />
            <div className="ab-title">AI-HUB</div>
            <div className="ab-org">Văn phòng Đăng ký đất đai thành phố Hà Nội</div>
            <div className="ab-desc">Số hóa &amp; đối soát Giấy Chứng Nhận Quyền sử dụng đất trực tiếp trên kho dữ liệu nội bộ.</div>
          </div>
          <div className="ab-foot">© {year} · VP Đăng ký đất đai TP Hà Nội</div>
        </aside>

        <section className="auth-form">
          <div className="af-brand">
            <img className="brand-logo sm" src="/logo-sotnmt.png" alt="" />
            <span>AI-HUB</span>
          </div>
          <div className="af-title">Đăng nhập</div>
          <div className="af-sub">Dùng tài khoản nội bộ để tiếp tục.</div>

          <form onSubmit={submit} noValidate>
            <label className="field-label" htmlFor="lg-u">Tài khoản</label>
            <input id="lg-u" className="text-input" value={username} autoFocus autoComplete="username"
              disabled={busy} onChange={(e) => setUsername(e.target.value)} />

            <label className="field-label" htmlFor="lg-p">Mật khẩu</label>
            <input id="lg-p" className="text-input" type="password" value={password} autoComplete="current-password"
              disabled={busy} onChange={(e) => setPassword(e.target.value)} />

            {error && <div className="auth-err"><Icon name="x" size={14} /> {error}</div>}

            <button className="primary login-btn" disabled={busy || !username || !password}>
              {busy
                ? (<><span className="spin" /> Đang đăng nhập…</>)
                : (<><Icon name="check" size={15} /> Đăng nhập</>)}
            </button>
          </form>
          <div className="af-foot">© {year} · VP Đăng ký đất đai TP Hà Nội</div>
        </section>
      </main>
    </div>
  );
}
