import React from "react";

const isObj = (v) => v && typeof v === "object";

// Đặt giá trị tại đường dẫn "a.b[0].c" trong bản sao (mutate). path "" = thay cả gốc.
export function setAt(root, path, val) {
  if (!path) return val;
  const tokens = path.replace(/\[(\d+)\]/g, ".$1").split(".").filter(Boolean);
  let cur = root;
  for (let i = 0; i < tokens.length - 1; i++) cur = cur[tokens[i]];
  cur[tokens[tokens.length - 1]] = val;
  return root;
}

// Render cây giá trị THÀNH ô NHẬP. Mảng-các-object → BẢNG sửa được (cột=trường,
// hàng=item, cuộn ngang+dọc, cột phủ value); mảng vô hướng → mục đánh số;
// object → hàng key; lá → input.
export default function EditableTree({ value, path, onLeaf }) {
  if (Array.isArray(value)) {
    const allObj = value.length > 0 && value.every((x) => x && typeof x === "object" && !Array.isArray(x));
    if (allObj) {
      const cols = [...new Set(value.flatMap((x) => Object.keys(x)))];
      return (
        <div className="ev-tablewrap">
          <table className="ev-table">
            <thead>
              <tr><th className="ev-rownum">#</th>{cols.map((c) => <th key={c}>{c}</th>)}</tr>
            </thead>
            <tbody>
              {value.map((row, i) => (
                <tr key={i}>
                  <td className="ev-rownum">{i + 1}</td>
                  {cols.map((c) => (
                    <td key={c}>
                      {isObj(row[c])
                        ? <EditableTree value={row[c]} path={`${path}[${i}].${c}`} onLeaf={onLeaf} />
                        : <input className="ev-input" value={row[c] == null ? "" : String(row[c])}
                            title={row[c] == null ? "" : String(row[c])}
                            size={Math.min(48, Math.max(8, String(row[c] ?? "").length + 1))}
                            onChange={(e) => onLeaf(`${path}[${i}].${c}`, e.target.value)} />}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }
    return (
      <div className="ev-array">
        {value.map((x, i) => (
          <div className="ev-item" key={i}>
            <span className="ev-idx">{i + 1}</span>
            <div className="ev-item-body"><EditableTree value={x} path={`${path}[${i}]`} onLeaf={onLeaf} /></div>
          </div>
        ))}
      </div>
    );
  }
  if (isObj(value)) {
    return (
      <div className="ev-obj">
        {Object.entries(value).map(([k, x]) => (
          <div className="ev-row" key={k}>
            <span className="ev-key">{k}</span>
            <div className="ev-val"><EditableTree value={x} path={path ? `${path}.${k}` : k} onLeaf={onLeaf} /></div>
          </div>
        ))}
      </div>
    );
  }
  return (
    <input className="ev-input" value={value == null ? "" : String(value)}
      title={value == null ? "" : String(value)}
      onChange={(e) => onLeaf(path, e.target.value)} />
  );
}
