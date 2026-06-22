import React, { useState } from "react";
import Icon from "./Icon.jsx";
import { auth } from "../api.js";
import { startEvents } from "../events.js";
import { toastOk } from "../toast.js";

// Khóa truy cập (nếu Sobagi bật bảo vệ console). Để trống nếu instance mở.
export default function Settings({ onClose }) {
  const [key, setKey] = useState(auth.apiKey);
  function save() {
    auth.apiKey = key.trim();
    startEvents();
    toastOk("Đã lưu khóa");
    onClose?.();
  }
  return (
    <div className="panel settings-panel">
      <h2>Cài đặt</h2>
      <label className="field">
        <span>Khóa truy cập (API key)</span>
        <input className="text-input" type="password" value={key}
          onChange={(e) => setKey(e.target.value)} placeholder="Liên hệ Sobagi để được cấp khóa" />
        <small className="muted">Để trống nếu console không bật bảo vệ.</small>
      </label>
      <button className="primary" onClick={save}><Icon name="check" size={15} /> Lưu</button>
    </div>
  );
}
