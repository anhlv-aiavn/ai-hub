import React, { useRef, useState } from "react";
import Icon from "./Icon.jsx";

// Vùng kéo-thả PDF có style (ẩn input gốc "Choose File"). Dùng chung Submit/Pipeline.
export default function Dropzone({ file, onPick, onClear }) {
  const [drag, setDrag] = useState(false);
  const ref = useRef(null);
  function pick(f) { if (f && f.type === "application/pdf") onPick(f); }

  return (
    <div className={`dropzone ${drag ? "over" : ""} ${file ? "has" : ""}`}
      onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); pick(e.dataTransfer.files?.[0]); }}
      onClick={() => ref.current?.click()}>
      <input ref={ref} type="file" accept="application/pdf" style={{ display: "none" }}
        onChange={(e) => pick(e.target.files?.[0])} />
      {file ? (
        <div className="file-chip">
          <Icon name="fileText" size={18} />
          <span className="fc-name">{file.name}</span>
          <span className="fc-size">{(file.size / 1024 / 1024).toFixed(2)} MB</span>
          <button type="button" className="icon-btn" onClick={(e) => { e.stopPropagation(); onClear?.(); }} aria-label="Bỏ tệp"><Icon name="x" size={15} /></button>
        </div>
      ) : (
        <>
          <Icon name="upload" size={26} />
          <div className="dz-title">Kéo thả tệp PDF vào đây, hoặc bấm để chọn</div>
          <div className="dz-sub">Định dạng PDF</div>
        </>
      )}
    </div>
  );
}
