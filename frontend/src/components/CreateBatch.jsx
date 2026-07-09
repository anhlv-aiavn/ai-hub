import React, { useEffect, useRef, useState } from "react";
import Icon from "./Icon.jsx";
import { createBatch, getBranches, getBrowseSources, getSettingsStatus } from "../api.js";
import { toastOk, toastErr, toastWarn } from "../toast.js";
import MinioBrowser from "./MinioBrowser.jsx";

// Số hóa: chọn chi nhánh + thả NHIỀU PDF ("Từ máy tính") hoặc duyệt kho MinIO
// nguồn có sẵn ("Từ kho S3") → 1 đợt → mỗi PDF chạy detect+extract.
// Operator: chi nhánh CỐ ĐỊNH theo tài khoản. Admin: chọn từ danh sách.
export default function CreateBatch({ user, onCreated }) {
  const isAdmin = user?.role === "admin";
  const [source, setSource] = useState("upload"); // "upload" | "minio"
  const [files, setFiles] = useState([]);
  const [branch, setBranch] = useState(isAdmin ? "" : (user?.branch || ""));
  const [branches, setBranches] = useState([]);
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pct, setPct] = useState(0);
  const [step, setStep] = useState("");
  const ref = useRef(null);

  const [minioSources, setMinioSources] = useState([]);
  const [minioSourceId, setMinioSourceId] = useState("");
  const [destConfigured, setDestConfigured] = useState(true); // lạc quan khi đang tải, tránh nháy banner

  useEffect(() => {
    if (isAdmin) getBranches().then((d) => setBranches(d.branches || [])).catch(() => {});
  }, [isAdmin]);

  useEffect(() => {
    getSettingsStatus().then((s) => setDestConfigured(!!s.destination_configured)).catch(() => {});
  }, []);

  useEffect(() => {
    getBrowseSources().then((d) => {
      const list = d.sources || [];
      setMinioSources(list);
      if (list.length === 1) setMinioSourceId(list[0].id);
    }).catch(() => {});
  }, []);

  function onMinioImported(batchId) {
    onCreated?.(batchId);
  }

  function addFiles(list) {
    const pdfs = Array.from(list || []).filter((f) => f.type === "application/pdf");
    if (!pdfs.length) return;
    setFiles((prev) => {
      const seen = new Set(prev.map((f) => f.name + f.size));
      const fresh = pdfs.filter((f) => !seen.has(f.name + f.size));
      const dupCount = pdfs.length - fresh.length;
      if (dupCount > 0) {
        toastWarn(dupCount === 1
          ? `Tệp "${pdfs.find((f) => seen.has(f.name + f.size))?.name}" đã có trong danh sách`
          : `${dupCount} tệp đã có trong danh sách, bỏ qua`);
      }
      return [...prev, ...fresh];
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
      if (b.failed?.length) toastErr(`Đã tạo đợt · ${b.file_count} hồ sơ. Lỗi ${b.failed.length} tệp: ${b.failed.join(", ")}`);
      else toastOk(`Đã tạo đợt · ${b.file_count} hồ sơ`);
      setFiles([]);
      onCreated?.(b.batch_id);
    } catch (e) { toastErr(e.message || e); } finally { setBusy(false); setPct(0); setStep(""); }
  }

  const totalMB = (files.reduce((s, f) => s + f.size, 0) / 1024 / 1024).toFixed(1);

  return (
    <div className="panel create-batch">
      <h2>Số hóa hồ sơ mới</h2>
      <p className="muted">Tải lên Giấy chứng nhận (PDF). Hệ thống tự nhận diện, trích xuất
        và gom trang bổ sung theo Số phát hành.</p>

      {!destConfigured && (
        <div className="admin-warn">
          <Icon name="alertTriangle" size={16} />
          <div>
            <b>Chưa cấu hình S3 đích</b>
            <p>Không thể số hóa hồ sơ mới cho tới khi admin cấu hình S3 đích
              {isAdmin ? ' ở "Cấu hình hệ thống → S3 đích".' : "."}</p>
          </div>
        </div>
      )}

      <label className="field-label" htmlFor="cb-branch">Chi nhánh / Đơn vị</label>
      {isAdmin ? (
        <select id="cb-branch" className="text-input" value={branch}
          onChange={(e) => setBranch(e.target.value)}>
          <option value="">— Chọn chi nhánh —</option>
          {branches.map((b) => <option key={b} value={b}>{b}</option>)}
        </select>
      ) : (
        <input id="cb-branch" className="text-input" value={branch} disabled readOnly />
      )}

      <div className="seg-toggle cb-source-toggle">
        <button type="button" className={source === "upload" ? "active" : ""} onClick={() => setSource("upload")}>
          <Icon name="upload" size={14} /> Từ máy tính
        </button>
        <button type="button" className={source === "minio" ? "active" : ""} onClick={() => setSource("minio")}>
          <Icon name="folder" size={14} /> Từ kho S3
        </button>
      </div>

      {source === "minio" && (
        <div className="cb-minio">
          {minioSources.length > 1 && (
            <select className="text-input" value={minioSourceId} onChange={(e) => setMinioSourceId(e.target.value)}>
              <option value="">— Chọn nguồn MinIO —</option>
              {minioSources.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          )}
          {!minioSources.length && <p className="muted small">Chưa có nguồn MinIO nào — thêm ở "Cấu hình hệ thống" (admin).</p>}
          {minioSourceId && branch && (
            <MinioBrowser sourceId={minioSourceId} branch={branch} onImported={onMinioImported} />
          )}
          {minioSourceId && !branch && <p className="muted small">Hãy chọn chi nhánh trước.</p>}
        </div>
      )}

      {source === "upload" && (
      <>
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
                {pct < 100 ? `Đang tải lên… ${pct}%` : "Đang khởi tạo đợt…"}
                {step && <span className="up-step"> · {step}</span>}
              </div>
            </div>
          )}
          <div className="cb-foot">
            <span className="muted">
              {files.length} tệp · {totalMB} MB{branch ? ` · ${branch}` : " · chưa chọn chi nhánh"}
            </span>
            <button className="primary" disabled={busy || !branch || !destConfigured} onClick={submit}>
              <Icon name="sparkles" size={15} /> {busy ? "Đang tải lên…" : "Bắt đầu số hóa"}
            </button>
          </div>
        </>
      )}
      </>
      )}
    </div>
  );
}
