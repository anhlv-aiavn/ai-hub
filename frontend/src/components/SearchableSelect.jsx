import React, { useEffect, useRef, useState } from "react";
import Icon from "./Icon.jsx";

// Dropdown gộp ô tìm kiếm ngay trong popup (không phải 1 ô input tách rời cạnh
// select) -- cơ chế popover tham khảo AccountMenu.jsx (trigger + click-outside/
// Escape để đóng), phần input+list tham khảo MinioBrowser.jsx.
export default function SearchableSelect({
  value, onChange, options, placeholder = "Chọn…", searchPlaceholder = "Tìm…",
  query, onQueryChange, className = "",
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    function onDoc(e) { if (!rootRef.current?.contains(e.target)) setOpen(false); }
    function onKey(e) { if (e.key === "Escape") setOpen(false); }
    document.addEventListener("mousedown", onDoc, true);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDoc, true);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open]);

  // Mở popup -> focus ngay vào ô tìm để gõ được luôn, khỏi phải bấm thêm lần nữa.
  useEffect(() => { if (open) inputRef.current?.focus(); }, [open]);

  const selected = options.find((o) => o.value === value);

  function pick(v) { onChange(v); setOpen(false); }

  return (
    <div className={`ssel ${className}`} ref={rootRef}>
      <button type="button" className="ssel-trigger" aria-haspopup="listbox" aria-expanded={open}
        onClick={() => setOpen((v) => !v)}>
        <span className="ssel-value">{selected ? selected.label : placeholder}</span>
        <Icon name="chevronDown" size={14} />
      </button>

      {open && (
        <div className="ssel-pop" role="listbox">
          <div className="ssel-search">
            <Icon name="search" size={14} />
            <input
              ref={inputRef}
              placeholder={searchPlaceholder}
              value={query}
              onChange={(e) => onQueryChange(e.target.value)}
            />
          </div>
          <div className="ssel-list">
            {options.map((o) => (
              <button
                type="button" key={o.value || "__all__"} role="option" aria-selected={o.value === value}
                className={`ssel-item${o.value === value ? " active" : ""}`}
                onClick={() => pick(o.value)}
              >
                {o.label}
              </button>
            ))}
            {options.length === 0 && <div className="ssel-empty">Không có kết quả</div>}
          </div>
        </div>
      )}
    </div>
  );
}
