import React, { useEffect, useState } from "react";
import { startEvents, stopEvents } from "./events.js";
import { auth, getMe, logout } from "./api.js";
import Icon from "./components/Icon.jsx";
import Toaster from "./components/Toaster.jsx";
import Login from "./components/Login.jsx";
import Users from "./components/Users.jsx";
import CreateBatch from "./components/CreateBatch.jsx";
import ExtractTable from "./components/ExtractTable.jsx";
import Reconcile from "./components/Reconcile.jsx";
import ExportView from "./components/ExportView.jsx";

const TABS = [
  ["create", "Số hóa"],
  ["table", "Kết quả trích xuất"],
  ["export", "Tổng quan"],
];

export default function App() {
  const [user, setUser] = useState(null);
  const [authReady, setAuthReady] = useState(false);
  const [tab, setTab] = useState("create");
  const [batchId, setBatchId] = useState(null);
  const [openGcn, setOpenGcn] = useState(null);
  const [showUsers, setShowUsers] = useState(false);

  // Khôi phục phiên từ token.
  useEffect(() => {
    if (!auth.token) { setAuthReady(true); return; }
    getMe().then((u) => { setUser(u); startEvents(); })
      .catch(() => { logout(); })
      .finally(() => setAuthReady(true));
    return () => stopEvents();
  }, []);

  function onLogin(u) { setUser(u); startEvents(); }
  function onLogout() { stopEvents(); logout(); setUser(null); setShowUsers(false); }
  function onCreated(id) { setBatchId(id); setOpenGcn(null); setTab("table"); }

  if (!authReady) return <div className="app wide"><div className="panel muted">Đang tải…</div></div>;
  if (!user) return (<><Login onLogin={onLogin} /><Toaster /></>);

  const isAdmin = user.role === "admin";

  return (
    <div className="app wide">
      <header className="topbar">
        <div className="brand">
          <span className="mark"><Icon name="layers" size={18} /></span>
          AI-HUB <small>Giấy Chứng Nhận</small>
        </div>
        <div className="top-right">
          <nav>
            {TABS.map(([k, label]) => (
              <button key={k} className={k === tab ? "tab active" : "tab"}
                onClick={() => { setTab(k); if (k === "table") setOpenGcn(null); }}>{label}</button>
            ))}
          </nav>
          <div className="user-chip" title={isAdmin ? "Admin · toàn hệ thống" : user.branch}>
            <Icon name="layers" size={13} />
            <span className="uc-name">{user.username}</span>
            <span className="uc-sub">{isAdmin ? "Admin" : (user.branch || "—")}</span>
          </div>
          {isAdmin && (
            <button className="icon-btn" title="Quản trị tài khoản" onClick={() => setShowUsers((v) => !v)}>
              <Icon name="sliders" size={18} />
            </button>
          )}
          <button className="icon-btn" title="Đăng xuất" onClick={onLogout}>
            <Icon name="x" size={18} />
          </button>
        </div>
      </header>

      <main>
        {showUsers && isAdmin && <Users me={user} onClose={() => setShowUsers(false)} />}
        {tab === "create" && <CreateBatch user={user} onCreated={onCreated} />}
        {tab === "table" && (
          openGcn
            ? <Reconcile gcnId={openGcn} onBack={() => setOpenGcn(null)} />
            : <ExtractTable user={user} batchId={batchId} onPickBatch={setBatchId} onOpen={setOpenGcn} />
        )}
        {tab === "export" && <ExportView user={user} />}
      </main>
      <Toaster />
    </div>
  );
}
