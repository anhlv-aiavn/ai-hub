import React from "react";

const isObj = (v) => v && typeof v === "object";

// Ô nhập lá: textarea TỰ GIÃN cao theo nội dung + WRAP — địa chỉ / nội dung biến
// động dài không còn bị bóp một dòng. Rộng linh hoạt theo ô chứa (CSS).
function Leaf({ value, path, onLeaf }) {
  const ref = React.useRef(null);
  const str = value == null ? "" : String(value);
  const fit = (el) => { if (el) { el.style.height = "auto"; el.style.height = `${el.scrollHeight}px`; } };
  React.useLayoutEffect(() => { fit(ref.current); }, [str]);
  return (
    <textarea
      ref={ref} className="ev-input" rows={1} value={str} title={str}
      onChange={(e) => { onLeaf(path, e.target.value); fit(e.target); }}
    />
  );
}

// Đặt giá trị tại đường dẫn "a.b[0].c" trong bản sao (mutate). path "" = thay cả gốc.
export function setAt(root, path, val) {
  if (!path) return val;
  const tokens = path.replace(/\[(\d+)\]/g, ".$1").split(".").filter(Boolean);
  let cur = root;
  for (let i = 0; i < tokens.length - 1; i++) cur = cur[tokens[i]];
  cur[tokens[tokens.length - 1]] = val;
  return root;
}

// Sắp cột: các cột trong `order` lên trước (đúng thứ tự), còn lại giữ nguyên thứ tự gốc.
function orderCols(cols, order) {
  if (!order || !order.length) return cols;
  const want = order.filter((c) => cols.includes(c));
  const rest = cols.filter((c) => !want.includes(c));
  return [...want, ...rest];
}

// Render cây giá trị THÀNH ô NHẬP. Mảng-các-object → BẢNG sửa được (cột=trường,
// hàng=item, cuộn ngang+dọc, cột phủ value); mảng vô hướng → mục đánh số;
// object → hàng key; lá → input.
// colsOrder: thứ tự cột ưu tiên cho bảng. skipKeys: bỏ qua các key này khi render object.
export default function EditableTree({ value, path, onLeaf, colsOrder, skipKeys }) {
  if (Array.isArray(value)) {
    const allObj = value.length > 0 && value.every((x) => x && typeof x === "object" && !Array.isArray(x));
    if (allObj) {
      const cols = orderCols([...new Set(value.flatMap((x) => Object.keys(x)))], colsOrder);
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
                        : <Leaf value={row[c]} path={`${path}[${i}].${c}`} onLeaf={onLeaf} />}
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
        {Object.entries(value).filter(([k]) => !skipKeys?.includes(k)).map(([k, x]) => (
          <div className="ev-row" key={k}>
            <span className="ev-key">{k}</span>
            <div className="ev-val"><EditableTree value={x} path={path ? `${path}.${k}` : k} onLeaf={onLeaf} /></div>
          </div>
        ))}
      </div>
    );
  }
  return <Leaf value={value} path={path} onLeaf={onLeaf} />;
}
