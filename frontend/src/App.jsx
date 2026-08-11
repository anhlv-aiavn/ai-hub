import React, { useEffect, useState } from "react";
import { startEvents, stopEvents } from "./events.js";
import { auth, getBranding, getMe, heartbeat, logout, sendSessionEndBeacon } from "./api.js";
import Toaster from "./components/Toaster.jsx";
import Login from "./components/Login.jsx";
import AccountMenu from "./components/AccountMenu.jsx";
import Users from "./components/Users.jsx";
import ChangePasswordModal from "./components/ChangePasswordModal.jsx";
import AdminSettings from "./components/AdminSettings.jsx";
import AuditLog from "./components/AuditLog.jsx";
import CreateBatch from "./components/CreateBatch.jsx";
import ExtractTable from "./components/ExtractTable.jsx";
import Reconcile from "./components/Reconcile.jsx";
import ExportView from "./components/ExportView.jsx";
import BatchManager from "./components/BatchManager.jsx";
import QcSync from "./components/QcSync.jsx";
import QcClassification from "./components/QcClassification.jsx";

const FALLBACK_BRANDING = {
  org_name: "VP Đăng ký đất đai TP Hà Nội",
  logo_url: "/logo-sotnmt.png",
  copyright_text: "VP Đăng ký đất đai TP Hà Nội",
};

const TABS = [
  ["create", "Số hóa", "operator"],
  ["table", "Kết quả trích xuất", "viewer"],
  ["export", "Tổng quan", "viewer"],
  ["manage", "Quản lý lô", "admin"],
  ["qcsync", "QC Sync", "admin"],
  ["qcclass", "Phân loại", "admin"],
  ["audit", "Audit log", "admin"],
];
const ROLE_RANK = { viewer: 0, operator: 1, admin: 2 };

export default function App() {
  const [user, setUser] = useState(null);
  const [authReady, setAuthReady] = useState(false);
  const [tab, setTab] = useState("export");
  const [batchId, setBatchId] = useState(null);
  const [openGcn, setOpenGcn] = useState(null);
  const [showUsers, setShowUsers] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [showChangePassword, setShowChangePassword] = useState(false);
  const [branding, setBranding] = useState(FALLBACK_BRANDING);

  function refreshBranding() {
    getBranding().then((b) => {
      if (b && (b.org_name || b.logo_url || b.copyright_text)) setBranding({ ...FALLBACK_BRANDING, ...b });
    }).catch(() => {});
  }

  useEffect(refreshBranding, []);

  // Đồng bộ favicon của tab trình duyệt với logo hiện tại.
  useEffect(() => {
    if (!branding.logo_url) return;
    let link = document.querySelector("link[rel~='icon']");
    if (!link) {
      link = document.createElement("link");
      link.rel = "icon";
      document.head.appendChild(link);
    }
    link.href = branding.logo_url;
  }, [branding.logo_url]);

  // Khôi phục phiên từ token.
  useEffect(() => {
    if (!auth.token) { setAuthReady(true); return; }
    getMe().then((u) => { setUser(u); startEvents(); })
      .catch(() => { logout(); })
      .finally(() => setAuthReady(true));
    return () => stopEvents();
  }, []);

  // Nhịp tim phiên: nuôi "đang hoạt động" cho admin thấy (Users.jsx) + phát
  // hiện bị đăng xuất cưỡng chế/thay phiên (401) để tự thoát ra màn đăng nhập.
  // sendSessionEndBeacon lúc rời tab đánh dấu "không hoạt động" ngay, không cần
  // chờ hết TTL — xử lý case "thoát web nhưng chưa bấm đăng xuất".
  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    async function beat() {
      try { await heartbeat(); }
      catch (e) { if (!cancelled && e.status === 401) onLogout(); }
    }
    beat();
    const id = setInterval(beat, 30000);
    window.addEventListener("pagehide", sendSessionEndBeacon);
    return () => {
      cancelled = true;
      clearInterval(id);
      window.removeEventListener("pagehide", sendSessionEndBeacon);
    };
  }, [user]);

  function onLogin(u) { setUser(u); startEvents(); }
  function onLogout() {
    stopEvents(); logout(); setUser(null);
    setShowUsers(false); setShowSettings(false);
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
            onChangePassword={() => setShowChangePassword(true)}
            onLogout={onLogout}
          />
        </div>
      </header>

      <main>
        {showUsers && isAdmin && <Users me={user} onClose={() => setShowUsers(false)} />}
        {showSettings && isAdmin && (
          <AdminSettings onClose={() => { setShowSettings(false); refreshBranding(); }} />
        )}
        {showChangePassword && <ChangePasswordModal onClose={() => setShowChangePassword(false)} />}
        {openGcn && <Reconcile user={user} gcnId={openGcn} onBack={() => setOpenGcn(null)} onOpen={setOpenGcn} />}
        {/* Ẩn (không unmount) khi đang xem chi tiết — ExtractTable giữ state bộ lọc
            (trạng thái/hậu kiểm/tài khoản/từ khóa/trang) cục bộ, unmount sẽ mất hết
            lúc quay lại từ Reconcile. */}
        <div style={openGcn ? { display: "none" } : undefined}>
          {tab === "create" && rank >= ROLE_RANK.operator && <CreateBatch user={user} onCreated={onCreated} />}
          {tab === "table" && (
            <ExtractTable user={user} batchId={batchId} onPickBatch={setBatchId} onOpen={setOpenGcn} />
          )}
          {tab === "export" && <ExportView user={user} />}
          {tab === "manage" && isAdmin && <BatchManager />}
          {tab === "qcsync" && isAdmin && <QcSync />}
          {tab === "qcclass" && isAdmin && <QcClassification />}
          {tab === "audit" && isAdmin && <AuditLog />}
        </div>
      </main>
      <Toaster />
    </div>
  );
}
