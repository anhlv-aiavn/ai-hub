import React, { useEffect, useRef, useState } from "react";
import Icon from "./Icon.jsx";
import { createBatch, getBranches } from "../api.js";
import { toastOk, toastErr } from "../toast.js";

// Tạo việc: chọn chi nhánh + thả NHIỀU PDF → 1 lô → mỗi PDF chạy detect+extract.
export default function CreateBatch({ onCreated }) {
  const [files, setFiles] = useState([]);
  const [branch, setBranch] = useState("");
  const [branches, setBranches] = useState([]);
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pct, setPct] = useState(0);
  const [step, setStep] = useState("");
  const ref = useRef(null);

  useEffect(() => { getBranches().then((d) => setBranches(d.branches || [])).catch(() => {}); }, []);

  function addFiles(list) {
    const pdfs = Array.from(list || []).filter((f) => f.type === "application/pdf");
    if (!pdfs.length) return;
    setFiles((prev) => {
      const seen = new Set(prev.map((f) => f.name + f.size));
      return [...prev, ...pdfs.filter((f) => !seen.has(f.name + f.size))];
    });
  }
  function removeAt(i) { setFiles((prev) => prev.filter((_, j) => j !== i)); }

  async function submit() {
    if (!files.length) return;
    if (!branch) { toastErr("Hãy chọn chi nhánh"); return; }
    setBusy(true); setPct(0); setStep("");
    try {
      const b = await createBatch({
        files, branch,
        onProgress: (p, meta) => {
          setPct(p);
          if (meta?.count) setStep(`Tệp ${meta.index}/${meta.count}${meta.name ? ` · ${meta.name}` : ""}`);
        },
      });
      if (b.failed?.length) toastErr(`Tạo lô · ${b.file_count} giấy. Lỗi ${b.failed.length} tệp: ${b.failed.join(", ")}`);
      else toastOk(`Đã tạo lô · ${b.file_count} giấy`);
      setFiles([]);
      onCreated?.(b.batch_id);
    } catch (e) { toastErr(e.message || e); } finally { setBusy(false); setPct(0); setStep(""); }
  }

  const totalMB = (files.reduce((s, f) => s + f.size, 0) / 1024 / 1024).toFixed(1);

  return (
    <div className="panel create-batch">
      <h2>Tạo việc mới</h2>
      <p className="muted">Thả vào nhiều Giấy Chứng Nhận (PDF). Hệ thống tự phát hiện GCN, bóc tách,
        và gom tờ bổ sung theo Số phát hành.</p>

      <label className="field-label" htmlFor="cb-branch">Chi nhánh / Đơn vị</label>
      <select id="cb-branch" className="text-input" value={branch}
        onChange={(e) => setBranch(e.target.value)}>
        <option value="">— Chọn chi nhánh —</option>
        {branches.map((b) => <option key={b} value={b}>{b}</option>)}
      </select>

      <div className={`dropzone ${drag ? "over" : ""} ${files.length ? "has" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); addFiles(e.dataTransfer.files); }}
        onClick={() => ref.current?.click()}>
        <input ref={ref} type="file" accept="application/pdf" multiple style={{ display: "none" }}
          onChange={(e) => addFiles(e.target.files)} />
        <Icon name="upload" size={26} />
        <div className="dz-title">Kéo thả PDF vào đây, hoặc bấm để chọn</div>
      </div>

      {files.length > 0 && (
        <>
          <div className="file-list">
            {files.map((f, i) => (
              <div className="file-chip" key={f.name + f.size}>
                <Icon name="fileText" size={16} />
                <span className="fc-name">{f.name}</span>
                <span className="fc-size">{(f.size / 1024 / 1024).toFixed(2)} MB</span>
                <button type="button" className="icon-btn" onClick={() => removeAt(i)} aria-label="Bỏ"><Icon name="x" size={14} /></button>
              </div>
            ))}
          </div>
          {busy && (
            <div className="upload-progress">
              <div className="up-bar"><div className="up-fill" style={{ width: `${pct}%` }} /></div>
              <div className="up-label">
                {pct < 100 ? `Đang tải lên… ${pct}%` : "Đang khởi tạo lô…"}
                {step && <span className="up-step"> · {step}</span>}
              </div>
            </div>
          )}
          <div className="cb-foot">
            <span className="muted">
              {files.length} tệp · {totalMB} MB{branch ? ` · ${branch}` : " · chưa chọn chi nhánh"}
            </span>
            <button className="primary" disabled={busy || !branch} onClick={submit}>
              <Icon name="sparkles" size={15} /> {busy ? "Đang tải lên…" : "Bóc tách lô"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
