import React, { useEffect, useState } from "react";
import { startEvents, stopEvents } from "./events.js";
import { auth, getMe, logout } from "./api.js";
import Toaster from "./components/Toaster.jsx";
import Login from "./components/Login.jsx";
import AccountMenu from "./components/AccountMenu.jsx";
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
          <img className="brand-logo sm" src="/logo-sotnmt.png" alt="Sở TN&MT Hà Nội" />
          AI-HUB <small>VP Đăng ký đất đai TP Hà Nội</small>
        </div>
        <div className="top-right">
          <nav>
            {TABS.map(([k, label]) => (
              <button key={k} className={k === tab ? "tab active" : "tab"}
                onClick={() => { setTab(k); if (k === "table") setOpenGcn(null); }}>{label}</button>
            ))}
          </nav>
          <AccountMenu
            user={user} isAdmin={isAdmin}
            onManageUsers={() => setShowUsers(true)}
            onLogout={onLogout}
          />
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
