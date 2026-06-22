import React, { useEffect, useMemo, useState } from "react";
import Icon from "./Icon.jsx";
import GcnPdf from "./GcnPdf.jsx";
import EditableTree, { setAt } from "./EditableTree.jsx";
import { getGcn, putReview, downloadGcn } from "../api.js";
import { toastOk, toastErr } from "../toast.js";

// Thứ tự cột ưu tiên khi hậu kiểm (các trường quan trọng lên trước, còn lại giữ sau).
const CHU_COLS = ["Loại đối tượng", "Tên chủ", "Loại giấy tờ", "Số giấy tờ", "Địa chỉ"];
const THUA_COLS = ["Số thứ tự thửa", "Số hiệu tờ bản đồ", "Diện tích", "Địa chỉ"];

// Đối soát 1 GCN: trái = ảnh PDF, phải = các trường bóc ra (sửa được).
// Hậu kiểm ghi review.overrides + display_name (raw extractions giữ nguyên).
export default function Reconcile({ gcnId, onBack }) {
  const [doc, setDoc] = useState(null);
  const [work, setWork] = useState([]);          // bản làm việc của extractions
  const [overrides, setOverrides] = useState({}); // path → value đã sửa
  const [name, setName] = useState("");
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState("");
  const [pdfOpen, setPdfOpen] = useState(true);

  useEffect(() => {
    if (!gcnId) return;
    let live = true;
    getGcn(gcnId).then((d) => {
      if (!live) return;
      setDoc(d);
      setWork(structuredClone(d.extractions || []));
      setOverrides((d.review && d.review.overrides) || {});
      setName((d.review && d.review.display_name) || d.group_key || "");
      setPage(1);
    }).catch((e) => toastErr(e.message || e));
    return () => { live = false; };
  }, [gcnId]);

  function onLeaf(path, val) {
    setWork((prev) => {
      const clone = structuredClone(prev);
      setAt(clone, path, val);
      return clone;
    });
    setOverrides((o) => ({ ...o, [path]: val }));
  }

  async function save(status) {
    setBusy(status || "save");
    try {
      await putReview(gcnId, {
        display_name: name.trim() || null,
        overrides,
        status: status || undefined,
      });
      toastOk(status === "reviewed" ? "Đã duyệt" : "Đã lưu");
    } catch (e) { toastErr(e.message || e); } finally { setBusy(""); }
  }

  const entries = useMemo(() => {
    const out = [];
    (work || []).forEach((rec, ri) => {
      const list = rec?.result?.["Đăng ký"];
      if (Array.isArray(list)) list.forEach((entry, ei) => out.push({ ri, ei, entry, rec }));
    });
    return out;
  }, [work]);

  // Render một khối của entry. Chủ sử dụng: đảo cột ưu tiên. Thửa đất: tách từng
  // thửa (trường chính + bảng Mục đích riêng). Còn lại: cây mặc định.
  function renderBlock(block, val, ri, ei) {
    const base = `[${ri}].result.Đăng ký[${ei}]`;
    if (block === "Chủ sử dụng" && Array.isArray(val)) {
      return <EditableTree value={val} path={`${base}.${block}`} onLeaf={onLeaf} colsOrder={CHU_COLS} />;
    }
    if (block === "Thửa đất" && Array.isArray(val)) {
      return (
        <div className="rc-thuas">
          {val.map((thua, ti) => (
            <div className="rc-thua" key={ti}>
              <div className="rc-thua-head">Thửa đất {ti + 1}</div>
              <EditableTree value={thua} path={`${base}.${block}[${ti}]`} onLeaf={onLeaf}
                skipKeys={["Mục đích sử dụng"]} colsOrder={THUA_COLS} />
              {Array.isArray(thua?.["Mục đích sử dụng"]) && thua["Mục đích sử dụng"].length > 0 && (
                <div className="rc-sub">
                  <div className="rc-sub-name">Mục đích sử dụng</div>
                  <EditableTree value={thua["Mục đích sử dụng"]}
                    path={`${base}.${block}[${ti}].Mục đích sử dụng`} onLeaf={onLeaf} />
                </div>
              )}
            </div>
          ))}
        </div>
      );
    }
    return <EditableTree value={val} path={`${base}.${block}`} onLeaf={onLeaf} />;
  }

  if (!doc) return <div className="panel muted">Đang tải…</div>;
  const dirty = Object.keys(overrides).length > 0 || name !== ((doc.review && doc.review.display_name) || doc.group_key || "");

  return (
    <div className="panel reconcile">
      <div className="rc-toolbar">
        <button className="ghost sm" onClick={onBack}><Icon name="chevronLeft" size={14} /> Bảng trích xuất</button>
        <span className={`badge st-${doc.status}`}>{doc.status}</span>
        <input className="rc-name" placeholder="Đặt tên giấy…" value={name}
          onChange={(e) => setName(e.target.value)} title="Tên hiển thị / tên file khi tải" />
        <span className="tb-gap" />
        <button className="ghost sm" onClick={() => setPdfOpen((v) => !v)}>
          <Icon name="image" size={14} /> {pdfOpen ? "Ẩn PDF" : "Hiện PDF"}
        </button>
        <button className="ghost sm" disabled={busy} onClick={() => save()}>
          {busy === "save" ? "…" : "Lưu"}
        </button>
        <button className="ghost sm" disabled={busy} onClick={() => save("reviewed")}>
          <Icon name="check" size={14} /> Duyệt
        </button>
        <button className="primary sm" onClick={() => downloadGcn(gcnId, `${(name || gcnId)}.zip`)}>
          <Icon name="download" size={14} /> Tải bộ
        </button>
      </div>

      <div className={`rc-body ${pdfOpen ? "with-pdf" : "no-pdf"}`}>
        {pdfOpen && (
          <div className="rc-left">
            <GcnPdf gcnId={gcnId} page={page} onPageChange={setPage} />
          </div>
        )}
        <div className="rc-right">
          {doc.error && <div className="rc-err">Lỗi: {doc.error}</div>}
          {!entries.length && <div className="muted">Không có dữ liệu bóc tách.</div>}
          {entries.map(({ ri, ei, entry }) => (
            <div className="rc-entry" key={`${ri}-${ei}`}>
              <div className="rc-entry-head">
                <Icon name="fileText" size={15} />
                Số phát hành: <b>{entry?.["Giấy chứng nhận"]?.["Số phát hành"] || "—"}</b>
              </div>
              {Object.entries(entry).map(([block, val]) => (
                <div className="rc-block" key={block}>
                  <div className="rc-block-name">{block}</div>
                  {renderBlock(block, val, ri, ei)}
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
