import React, { useEffect, useState } from "react";
import { startEvents, stopEvents } from "./events.js";
import { auth, getBranding, getMe, logout } from "./api.js";
import Toaster from "./components/Toaster.jsx";
import Login from "./components/Login.jsx";
import AccountMenu from "./components/AccountMenu.jsx";
import Users from "./components/Users.jsx";
import AdminSettings from "./components/AdminSettings.jsx";
import AuditLog from "./components/AuditLog.jsx";
import CreateBatch from "./components/CreateBatch.jsx";
import ExtractTable from "./components/ExtractTable.jsx";
import Reconcile from "./components/Reconcile.jsx";
import ExportView from "./components/ExportView.jsx";

const FALLBACK_BRANDING = {
  org_name: "VP Đăng ký đất đai TP Hà Nội",
  logo_url: "/logo-sotnmt.png",
  copyright_text: "VP Đăng ký đất đai TP Hà Nội",
};

const TABS = [
  ["create", "Số hóa", "operator"],
  ["table", "Kết quả trích xuất", "viewer"],
  ["export", "Tổng quan", "viewer"],
];
const ROLE_RANK = { viewer: 0, operator: 1, admin: 2 };

export default function App() {
  const [user, setUser] = useState(null);
  const [authReady, setAuthReady] = useState(false);
  const [tab, setTab] = useState("table");
  const [batchId, setBatchId] = useState(null);
  const [openGcn, setOpenGcn] = useState(null);
  const [showUsers, setShowUsers] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [showAuditLog, setShowAuditLog] = useState(false);
  const [branding, setBranding] = useState(FALLBACK_BRANDING);

  useEffect(() => {
    getBranding().then((b) => {
      if (b && (b.org_name || b.logo_url || b.copyright_text)) setBranding({ ...FALLBACK_BRANDING, ...b });
    }).catch(() => {});
  }, []);

  // Khôi phục phiên từ token.
  useEffect(() => {
    if (!auth.token) { setAuthReady(true); return; }
    getMe().then((u) => { setUser(u); startEvents(); })
      .catch(() => { logout(); })
      .finally(() => setAuthReady(true));
    return () => stopEvents();
  }, []);

  function onLogin(u) { setUser(u); startEvents(); }
  function onLogout() {
    stopEvents(); logout(); setUser(null);
    setShowUsers(false); setShowSettings(false); setShowAuditLog(false);
  }
  function onCreated(id) { setBatchId(id); setOpenGcn(null); setTab("table"); }

  if (!authReady) return <div className="app wide"><div className="panel muted">Đang tải…</div></div>;
  if (!user) return (<><Login onLogin={onLogin} /><Toaster /></>);

  const isAdmin = user.role === "admin";
  const rank = ROLE_RANK[user.role] ?? 0;
  // "create" bị lọc khỏi nav cho viewer → không có đường click vào, không cần guard runtime.
  const tabs = TABS.filter(([, , minRole]) => rank >= (ROLE_RANK[minRole] ?? 0));

  return (
    <div className="app wide">
      <header className="topbar">
        <div className="brand">
          <img className="brand-logo sm" src={branding.logo_url} alt={branding.org_name} />
          AI-HUB <small>{branding.org_name}</small>
        </div>
        <div className="top-right">
          <nav>
            {tabs.map(([k, label]) => (
              <button key={k} className={k === tab ? "tab active" : "tab"}
                onClick={() => { setTab(k); setOpenGcn(null); }}>{label}</button>
            ))}
          </nav>
          <AccountMenu
            user={user} isAdmin={isAdmin}
            onManageUsers={() => setShowUsers(true)}
            onOpenSettings={() => setShowSettings(true)}
            onOpenAuditLog={() => setShowAuditLog(true)}
            onLogout={onLogout}
          />
        </div>
      </header>

      <main>
        {showUsers && isAdmin && <Users me={user} onClose={() => setShowUsers(false)} />}
        {showSettings && isAdmin && <AdminSettings onClose={() => setShowSettings(false)} />}
        {showAuditLog && isAdmin && <AuditLog onClose={() => setShowAuditLog(false)} />}
        {openGcn ? (
          <Reconcile gcnId={openGcn} onBack={() => setOpenGcn(null)} />
        ) : (
          <>
            {tab === "create" && rank >= ROLE_RANK.operator && <CreateBatch user={user} onCreated={onCreated} />}
            {tab === "table" && (
              <ExtractTable user={user} batchId={batchId} onPickBatch={setBatchId} onOpen={setOpenGcn} />
            )}
            {tab === "export" && <ExportView user={user} />}
          </>
        )}
      </main>
      <Toaster />
    </div>
  );
}
