import React, { useEffect, useState } from "react";
import Icon from "./Icon.jsx";
import Modal from "./Modal.jsx";
import {
  getQcSyncConfigs, getQcClassifications, reclassifyQcCut,
  getQcRefinedPayload, qcSyncCutPdfUrl,
} from "../api.js";
import { toastOk, toastErr } from "../toast.js";

// Trang "Phân loại" (admin) — phase 2 QC Sync: "làm mịn dữ liệu" (chuẩn hoá OCR
// thành payload) + phân loại cấu trúc hồ sơ (số chủ/số thửa/đa mục đích/
// chung-riêng), port thuật toán từ dự án nội bộ vpdd-don-ai — xem
// docs/algorithm.md §10. Cả 2 bước đều THUẦN PYTHON (không gọi API ngoài nào),
// worker tính TỰ ĐỘNG ngay sau OCR xong; trang này chỉ xem lại + chạy lại thủ
// công + xuất JSON để copy/tải (KHÔNG tự gọi API "kiểm tra đơn" của HSQ — ai-hub
// không có item_id/token hệ thống đó).
const STRUCTURAL_LABEL_OPTIONS = [
  "1 giấy 1 chủ 1 thửa",
  "1 giấy 1 chủ 1 thửa đa mục đích",
  "1 giấy nhiều chủ 1 thửa",
  "1 giấy nhiều chủ 1 thửa đa mục đích",
  "Có sử dụng chung, riêng",
];

const PAGE_SIZE = 50;

export default function QcClassification() {
  const [configs, setConfigs] = useState([]);
  const [configId, setConfigId] = useState("");
  const [q, setQ] = useState("");
  const [structuralLabel, setStructuralLabel] = useState("");
  const [rows, setRows] = useState([]);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [busyKey, setBusyKey] = useState(""); // `${itemId}:${cutIndex}` đang "Phân loại lại"
  const [jsonModal, setJsonModal] = useState(null); // {itemId, cutIndex, soGcn, data} | null

  useEffect(() => { getQcSyncConfigs().then(setConfigs).catch(() => {}); }, []);
  useEffect(() => { setPage(1); }, [configId, q, structuralLabel]);

  async function load() {
    setLoading(true);
    try {
      const res = await getQcClassifications({ configId, q, structuralLabel, page, pageSize: PAGE_SIZE });
      setRows(res.rows || []);
    } catch (e) { toastErr(e.message || e); }
    finally { setLoading(false); }
  }
  useEffect(() => { load(); }, [configId, q, structuralLabel, page]); // eslint-disable-line react-hooks/exhaustive-deps

  async function reclassify(row) {
    const key = `${row.item_id}:${row.cut_index}`;
    setBusyKey(key);
    try {
      const res = await reclassifyQcCut(row.item_id, row.cut_index);
      setRows((prev) => prev.map((r) => (
        r.item_id === row.item_id && r.cut_index === row.cut_index
          ? { ...r, classification: res.classification } : r
      )));
      toastOk("Đã phân loại lại");
    } catch (e) { toastErr(e.message || e); }
    finally { setBusyKey(""); }
  }

  async function openJson(row) {
    try {
      const data = await getQcRefinedPayload(row.item_id, row.cut_index);
      setJsonModal({ itemId: row.item_id, cutIndex: row.cut_index, soGcn: row.so_phat_hanh || row.name, data });
    } catch (e) { toastErr(e.message || e); }
  }

  function copyJson() {
    if (!jsonModal) return;
    navigator.clipboard.writeText(JSON.stringify(jsonModal.data, null, 2))
      .then(() => toastOk("Đã copy JSON"))
      .catch(() => toastErr("Không copy được (trình duyệt chặn clipboard)"));
  }

  function downloadJson() {
    if (!jsonModal) return;
    const blob = new Blob([JSON.stringify(jsonModal.data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${(jsonModal.soGcn || "payload").replace(/[/\\]/g, "-")}-payload.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  return (
    <div className="panel">
      <div className="panel-head">
        <h3><Icon name="layers" size={16} /> Phân loại hồ sơ</h3>
      </div>
      <p className="muted small" style={{ padding: "0 16px" }}>
        Sau khi OCR xong (pipeline "QC Sync"), hệ thống tự "làm mịn dữ liệu" (chuẩn hoá thành
        payload) và phân loại cấu trúc hồ sơ (số chủ/số thửa/đa mục đích/chung-riêng) — miễn phí,
        không gọi API ngoài nào. Bấm "Xem JSON" để copy/tải payload đã chuẩn hoá.
      </p>

      <div className="row" style={{ gap: 8, padding: "0 16px 12px", flexWrap: "wrap" }}>
        <select className="text-input" style={{ width: "auto" }} value={configId}
          onChange={(e) => setConfigId(e.target.value)}>
          <option value="">— Mọi kênh —</option>
          {configs.map((c) => <option key={c.id} value={c.id}>{c.ward_name || c.name}</option>)}
        </select>
        <input className="text-input" style={{ width: 220 }} placeholder="Tìm theo Số phát hành…"
          value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="text-input" style={{ width: "auto" }} value={structuralLabel}
          onChange={(e) => setStructuralLabel(e.target.value)}>
          <option value="">— Mọi nhãn cấu trúc —</option>
          {STRUCTURAL_LABEL_OPTIONS.map((l) => <option key={l} value={l}>{l}</option>)}
        </select>
      </div>

      <div className="tbl-dense qc-cls-tbl">
        <div className="file-row qc-cls-row qc-cls-head">
          <span>Số phát hành</span><span>Nhãn cấu trúc</span><span>Số chủ</span>
          <span>Số thửa</span><span>Đa mục đích</span><span>Chung/riêng</span><span>Thao tác</span>
        </div>
        {rows.map((r) => {
          const cls = r.classification || {};
          const key = `${r.item_id}:${r.cut_index}`;
          return (
            <div className="file-row qc-cls-row" key={key}>
              <a className="fr-name" href={qcSyncCutPdfUrl(r.item_id, r.cut_index)} target="_blank"
                rel="noopener noreferrer" title={`Xem file đã cắt: ${r.name || ""}`}>
                {r.so_phat_hanh || r.name || "—"}
              </a>
              <span className="fr-meta qc-reason-cell" title={(cls.NhanCauTruc || []).join(", ")}>
                {(cls.NhanCauTruc || []).join(", ") || "—"}
              </span>
              <span className="fr-meta">{cls.SoChuSoHuu ?? "—"}</span>
              <span className="fr-meta">{cls.SoThua ?? "—"}</span>
              <span className="fr-meta">{cls.CoDaMucDich ? "Có" : "Không"}</span>
              <span className="fr-meta">{cls.CoSuDungChungVaRieng ? "Có" : "Không"}</span>
              <span className="s3-actions">
                <button className="ghost xs" onClick={() => openJson(r)}>
                  <Icon name="fileText" size={12} /> Xem JSON
                </button>
                <button className="ghost xs" disabled={busyKey === key} onClick={() => reclassify(r)}>
                  <Icon name="refresh" size={12} /> Phân loại lại
                </button>
              </span>
            </div>
          );
        })}
        {!loading && !rows.length && <div className="muted center" style={{ padding: 16 }}>Chưa có dữ liệu.</div>}
      </div>
      <div className="row" style={{ gap: 8, margin: "8px 16px" }}>
        <button className="ghost xs" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>← Trang trước</button>
        <span className="muted small">Trang {page}</span>
        <button className="ghost xs" disabled={rows.length < PAGE_SIZE} onClick={() => setPage((p) => p + 1)}>Trang sau →</button>
      </div>

      {jsonModal && (
        <Modal title={`Payload đã làm mịn — ${jsonModal.soGcn || ""}`} onClose={() => setJsonModal(null)} wide>
          <div className="row" style={{ gap: 8, marginBottom: 8 }}>
            <button className="ghost sm" onClick={copyJson}><Icon name="copy" size={13} /> Copy JSON</button>
            <button className="ghost sm" onClick={downloadJson}><Icon name="download" size={13} /> Tải JSON</button>
          </div>
          <pre className="qc-json-view">{JSON.stringify(jsonModal.data, null, 2)}</pre>
        </Modal>
      )}
    </div>
  );
}
