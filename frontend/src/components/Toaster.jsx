import React, { useEffect, useState } from "react";
import { subscribe } from "../toast.js";
import Icon from "./Icon.jsx";

export default function Toaster() {
  const [items, setItems] = useState([]);
  useEffect(() =>
    subscribe((t) => {
      setItems((x) => [...x, t]);
      setTimeout(() => setItems((x) => x.filter((i) => i.id !== t.id)), 3400);
    }), []);
  return (
    <div className="toaster">
      {items.map((t) => (
        <div key={t.id} className={`toast ${t.type}`}>
          <Icon name={t.type === "err" ? "x" : "check"} size={15} />
          <span>{t.message}</span>
        </div>
      ))}
    </div>
  );
}
