import React, { useEffect, useState } from "react";
import { startEvents, stopEvents } from "./events.js";
import Icon from "./components/Icon.jsx";
import Toaster from "./components/Toaster.jsx";
import CreateBatch from "./components/CreateBatch.jsx";
import ExtractTable from "./components/ExtractTable.jsx";
import Reconcile from "./components/Reconcile.jsx";
import ExportView from "./components/ExportView.jsx";
import Settings from "./components/Settings.jsx";

const TABS = [
  ["create", "Tạo việc"],
  ["table", "Bảng trích xuất"],
  ["export", "Xuất dữ liệu"],
];

export default function App() {
  const [tab, setTab] = useState("create");
  const [batchId, setBatchId] = useState(null);
  const [openGcn, setOpenGcn] = useState(null);
  const [showSettings, setShowSettings] = useState(false);

  useEffect(() => { startEvents(); return () => stopEvents(); }, []);

  function onCreated(id) { setBatchId(id); setOpenGcn(null); setTab("table"); }

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
          <button className="icon-btn" title="Cài đặt" onClick={() => setShowSettings((v) => !v)}>
            <Icon name="sliders" size={18} />
          </button>
        </div>
      </header>

      <main>
        {showSettings && <Settings onClose={() => setShowSettings(false)} />}
        {tab === "create" && <CreateBatch onCreated={onCreated} />}
        {tab === "table" && (
          openGcn
            ? <Reconcile gcnId={openGcn} onBack={() => setOpenGcn(null)} />
            : <ExtractTable batchId={batchId} onPickBatch={setBatchId} onOpen={setOpenGcn} />
        )}
        {tab === "export" && <ExportView />}
      </main>
      <Toaster />
    </div>
  );
}
