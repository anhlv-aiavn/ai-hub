import React, { useEffect, useRef, useState } from "react";
import { subscribe } from "../toast.js";
import Icon from "./Icon.jsx";

// Chỉ hiện 1 thông báo tại một thời điểm — thông báo mới thay thế thông báo cũ
// thay vì chồng nhiều toast (vd bấm nút "Tạo" liên tiếp).
export default function Toaster() {
  const [item, setItem] = useState(null);
  const timerRef = useRef(null);
  useEffect(() =>
    subscribe((t) => {
      clearTimeout(timerRef.current);
      setItem(t);
      timerRef.current = setTimeout(
        () => setItem((cur) => (cur && cur.id === t.id ? null : cur)), 3400);
    }), []);
  if (!item) return null;
  return (
    <div className="toaster">
      <div className={`toast ${item.type}`}>
        <Icon name={item.type === "err" ? "x" : item.type === "warn" ? "alertTriangle" : "check"} size={15} />
        <span>{item.message}</span>
      </div>
    </div>
  );
}
