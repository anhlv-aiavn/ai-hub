import React, { useEffect, useRef, useState } from "react";
import Icon from "./Icon.jsx";

const ROLE_LABEL = { admin: "Admin", operator: "Operator", viewer: "Viewer" };

// Menu tài khoản kiểu popover (tham khảo datalens-agent): avatar ▾ → header + item.
export default function AccountMenu({ user, isAdmin, onManageUsers, onOpenSettings, onOpenAuditLog, onLogout }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const initial = (user.username || "?").trim().charAt(0).toUpperCase() || "?";
  const roleLabel = ROLE_LABEL[user.role] || user.role;
  const sub = isAdmin ? "Admin · toàn hệ thống" : `${roleLabel} · ${user.branch || "—"}`;

  useEffect(() => {
    if (!open) return;
    function onDoc(e) { if (!rootRef.current?.contains(e.target)) setOpen(false); }
    function onKey(e) { if (e.key === "Escape") setOpen(false); }
    document.addEventListener("mousedown", onDoc, true);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDoc, true);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open]);

  function pick(fn) { setOpen(false); fn?.(); }

  return (
    <div className="acct" ref={rootRef}>
      <button className="acct-trigger" aria-haspopup="menu" aria-expanded={open}
        title={user.username} onClick={() => setOpen((v) => !v)}>
        <span className="acct-ava">{initial}</span>
        <Icon name="chevronDown" size={14} />
      </button>

      {open && (
        <div className="acct-pop" role="menu">
          <div className="acct-head">
            <span className="acct-ava lg">{initial}</span>
            <span className="acct-id">
              <span className="acct-name">{user.username}</span>
              <span className="acct-sub">{sub}</span>
            </span>
          </div>
          <div className="acct-sep" />
          {isAdmin && (
            <button className="acct-item" role="menuitem" onClick={() => pick(onManageUsers)}>
              <Icon name="sliders" size={16} /> <span>Quản trị tài khoản</span>
            </button>
          )}
          {isAdmin && (
            <button className="acct-item" role="menuitem" onClick={() => pick(onOpenSettings)}>
              <Icon name="settings" size={16} /> <span>Cấu hình hệ thống</span>
            </button>
          )}
          {isAdmin && (
            <button className="acct-item" role="menuitem" onClick={() => pick(onOpenAuditLog)}>
              <Icon name="fileText" size={16} /> <span>Audit log</span>
            </button>
          )}
          <button className="acct-item is-danger" role="menuitem" onClick={() => pick(onLogout)}>
            <Icon name="logOut" size={16} /> <span>Đăng xuất</span>
          </button>
        </div>
      )}
    </div>
  );
}
