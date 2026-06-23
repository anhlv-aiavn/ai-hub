import React, { useEffect, useMemo, useState } from "react";
import Icon from "./Icon.jsx";
import GcnPdf from "./GcnPdf.jsx";
import EditableTree, { setAt } from "./EditableTree.jsx";
import { getGcn, putReview, downloadGcn } from "../api.js";
import { toastOk, toastErr } from "../toast.js";

// Thứ tự cột ưu tiên khi hậu kiểm (các trường quan trọng lên trước, còn lại giữ sau).
const CHU_COLS = ["Loại đối tượng", "Tên chủ", "Loại giấy tờ", "Số giấy tờ", "Địa chỉ"];
const THUA_COLS = ["Số thứ tự thửa", "Số hiệu tờ bản đồ", "Diện tích", "Địa chỉ"];
const NHA_COLS = ["Loại tài sản gắn liền với đất", "Diện tích xây dựng", "Diện tích sàn",
  "Địa chỉ", "Kết cấu", "Số tầng"];

// Index trang 0-based → chuỗi 1-based gọn, gộp đoạn liên tiếp: [0,1,2,4] → "1–3, 5".
function pageRange(idx) {
  const ns = [...new Set((idx || []).filter((n) => Number.isInteger(n)).map((n) => n + 1))].sort((a, b) => a - b);
  if (!ns.length) return "";
  const parts = [];
  let s = ns[0], p = ns[0];
  for (let i = 1; i < ns.length; i++) {
    if (ns[i] === p + 1) p = ns[i];
    else { parts.push(s === p ? `${s}` : `${s}–${p}`); s = p = ns[i]; }
  }
  parts.push(s === p ? `${s}` : `${s}–${p}`);
  return parts.join(", ");
}

// Đối soát 1 GCN: trái = ảnh PDF, phải = các trường bóc ra (sửa được).
// Hậu kiểm ghi review.overrides + display_name (raw extractions giữ nguyên).
export default function Reconcile({ gcnId, onBack }) {
  const [doc, setDoc] = useState(null);
  const [work, setWork] = useState([]);          // bản làm việc của extractions
  const [overrides, setOverrides] = useState({}); // path → value đã sửa
  const [deleted, setDeleted] = useState([]);    // chỉ số bản ghi GCN đã xoá
  const [name, setName] = useState("");
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState("");
  const [pdfOpen, setPdfOpen] = useState(true);

  useEffect(() => {
    if (!gcnId) return;
    let live = true;
    getGcn(gcnId).then((d) => {
      if (!live) return;
      const ov = (d.review && d.review.overrides) || {};
      // Áp overrides ĐÃ LƯU lên bản làm việc để mở lại thấy đúng giá trị đã sửa.
      const w = structuredClone(d.extractions || []);
      for (const [path, val] of Object.entries(ov)) {
        try { setAt(w, path, val); } catch { /* path lệch → bỏ qua */ }
      }
      setDoc(d);
      setWork(w);
      setOverrides(ov);
      setDeleted((d.review && d.review.deleted) || []);
      setName((d.review && d.review.display_name) || "");  // để trống → tên tệp tự theo SPH
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
        deleted,
        status: status || undefined,
      });
      toastOk(status === "reviewed" ? "Đã duyệt" : "Đã lưu");
    } catch (e) { toastErr(e.message || e); } finally { setBusy(""); }
  }

  async function removeGcn(ri) {
    if (!window.confirm("Xoá giấy chứng nhận này khỏi hồ sơ? (raw vẫn được giữ để truy lại)")) return;
    const nd = [...new Set([...deleted, ri])].sort((a, b) => a - b);
    setBusy("del");
    try {
      await putReview(gcnId, { display_name: name.trim() || null, overrides, deleted: nd });
      setDeleted(nd);
      toastOk("Đã xoá giấy chứng nhận");
    } catch (e) { toastErr(e.message || e); } finally { setBusy(""); }
  }

  const entries = useMemo(() => {
    const out = [];
    (work || []).forEach((rec, ri) => {
      if (deleted.includes(ri)) return;
      const list = rec?.result?.["Đăng ký"];
      if (Array.isArray(list)) list.forEach((entry, ei) => out.push({ ri, ei, entry, rec }));
    });
    return out;
  }, [work, deleted]);

  // Render một khối của entry. Chủ sử dụng: đảo cột ưu tiên. Thửa đất: tách từng
  // thửa (trường chính + bảng Mục đích riêng). Còn lại: cây mặc định.
  function renderBlock(block, val, ri, ei) {
    const base = `[${ri}].result.Đăng ký[${ei}]`;
    if (block === "Chủ sử dụng" && Array.isArray(val)) {
      return <EditableTree value={val} path={`${base}.${block}`} onLeaf={onLeaf} colsOrder={CHU_COLS} />;
    }
    if (block === "Thông tin nhà ở" && Array.isArray(val)) {
      return <EditableTree value={val} path={`${base}.${block}`} onLeaf={onLeaf} colsOrder={NHA_COLS} />;
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
  const dirty = Object.keys(overrides).length > 0 || name !== ((doc.review && doc.review.display_name) || "");

  return (
    <div className="panel reconcile">
      <div className="rc-toolbar">
        <button className="ghost sm" onClick={onBack}><Icon name="chevronLeft" size={14} /> Kết quả trích xuất</button>
        <span className={`badge st-${doc.status}`}>{doc.status}</span>
        <input className="rc-name" placeholder="Đặt tên hồ sơ…" value={name}
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
          <Icon name="download" size={14} /> Tải hồ sơ
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
          {entries.map(({ ri, ei, entry, rec }) => {
            const range = pageRange(rec?.page_indices);
            const firstPage = Math.min(...(rec?.page_indices || [0]).filter(Number.isInteger)) + 1;
            return (
            <div className="rc-entry" key={`${ri}-${ei}`}>
              <div className="rc-entry-head">
                <Icon name="fileText" size={15} />
                Số phát hành: <b>{entry?.["Giấy chứng nhận"]?.["Số phát hành"] || "—"}</b>
                {range && (
                  <button type="button" className="rc-range" title="Tới trang gốc đầu của giấy này"
                    onClick={() => { setPdfOpen(true); setPage(firstPage); }}>
                    Trang gốc {range}
                  </button>
                )}
                <button type="button" className="rc-del" disabled={busy} title="Xoá giấy chứng nhận này"
                  onClick={() => removeGcn(ri)}>
                  <Icon name="trash" size={14} />
                </button>
              </div>
              {Object.entries(entry).map(([block, val]) => (
                <div className="rc-block" key={block}>
                  <div className="rc-block-name">{block}</div>
                  {renderBlock(block, val, ri, ei)}
                </div>
              ))}
            </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
