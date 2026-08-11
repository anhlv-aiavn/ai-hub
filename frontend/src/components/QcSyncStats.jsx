import React, { useEffect, useMemo, useState } from "react";
import Icon from "./Icon.jsx";
import Modal from "./Modal.jsx";
import {
  getQcSyncConfigs, getQcSyncStats, getQcSyncStatsSeries, getQcSyncStatsByConfig, getQcSyncItems,
  qcSyncSourcePdfUrl, qcSyncCutPdfUrl,
} from "../api.js";
import { toastErr } from "../toast.js";

// Thống kê pipeline QC Sync (F-16, xem docs/algorithm.md §9) trên trang Tổng
// quan — viewer trở lên xem được, khác trang quản trị "QC Sync" (admin-only,
// có thêm thao tác Chạy lại/Xóa/Dừng — ở đây CHỈ xem, không sửa dữ liệu).
// Không có chart library trong repo (chỉ react/vite) — tự vẽ bar chart SVG
// nhẹ, theo skill dataviz: 1 trục, màu status-palette có sẵn (--ok/--warn/
// --err/--info), legend cho ≥2 chuỗi, hover tooltip + nhãn số trực tiếp trên
// cột, "Tổng" = khối KPI/hero chứ không phải bar chart cho 1 giá trị duy nhất.

const RANGES = [
  ["day", "Ngày", 30],
  ["week", "Tuần", 90],
  ["month", "Tháng", 365],
  ["all", "Tổng", null],
];

const QC_SERIES = [
  { key: "pass", label: "Đạt", color: "var(--ok)" },
  { key: "warn", label: "Đạt (cảnh báo)", color: "var(--warn)" },
  { key: "fail", label: "Không đạt", color: "var(--err)" },
];
const OCR_SERIES = [
  { key: "ocr_done", label: "Đã cắt", color: "var(--ok)" },
  { key: "no_gcn", label: "Không thấy GCN", color: "var(--warn)" },
  { key: "no_file", label: "Không thấy file", color: "var(--info)" },
  { key: "error", label: "Lỗi", color: "var(--err)" },
];
const CUTS_SERIES = [{ key: "cuts_created", label: "File đã cắt", color: "var(--accent)" }];

const VERDICT_LABEL = { pass: "Đạt", warn: "Đạt (cảnh báo)", fail: "Không đạt" };
const VERDICT_CLASS = { pass: "dot-ok", warn: "dot-unknown", fail: "dot-err" };

const fmt = (n) => (n || 0).toLocaleString("vi-VN");
const pct = (n, total) => (total ? Math.round(((n || 0) / total) * 1000) / 10 : 0);
// Dạng GỌN cho nhãn trong biểu đồ (chỗ hẹp) — số < 1000 vẫn hiện đầy đủ có
// dấu phân cách; từ hàng nghìn/triệu trở lên rút gọn "12,3k"/"4,5tr" cho vừa
// cột hẹp. Số liệu CHÍNH XÁC đầy đủ vẫn có ở tooltip hover + khối KPI/hero.
function fmtCompact(n) {
  const v = n || 0;
  const av = Math.abs(v);
  if (av < 1000) return v.toLocaleString("vi-VN");
  const useTr = av >= 999_950; // làm tròn 1 chữ số thập phân có thể vọt lên "1000k" — đẩy sang "tr"
  const unit = useTr ? [1_000_000, "tr"] : [1000, "k"];
  const rounded = Math.round((v / unit[0]) * 10) / 10;
  return `${rounded.toLocaleString("vi-VN")}${unit[1]}`;
}
function fmtDate(d) {
  if (!d) return "—";
  try { return new Date(d).toLocaleString("vi-VN"); } catch { return String(d); }
}

// ── Gom ngày → tuần/tháng ────────────────────────────────────────────────
function isoWeekKey(dateStr) {
  const d = new Date(`${dateStr}T00:00:00Z`);
  const dow = (d.getUTCDay() + 6) % 7; // 0=Thứ 2
  d.setUTCDate(d.getUTCDate() - dow); // lùi về Thứ 2 đầu tuần
  return d.toISOString().slice(0, 10);
}
const dd = (d) => `${String(d.getUTCDate()).padStart(2, "0")}/${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
function dayLabel(dateStr) { const [, m, day] = dateStr.split("-"); return `${day}/${m}`; }
function monthLabel(key) { const [y, m] = key.split("-"); return `Thg ${Number(m)}/${y}`; }
// Nhãn TUẦN là 1 khoảng ngày (Thứ 2–Chủ nhật), KHÔNG phải 1 ngày đơn — trước
// đây chỉ hiện ngày Thứ 2 đầu tuần khiến người xem tưởng nhầm là "hôm nay"
// (vd hôm nay 11/08 nhưng cột tuần lại ghi 10/08, gây hiểu lầm là sai số liệu).
function weekRangeLabel(mondayKey) {
  const start = new Date(`${mondayKey}T00:00:00Z`);
  const end = new Date(start);
  end.setUTCDate(end.getUTCDate() + 6);
  return `${dd(start)}–${dd(end)}`;
}

// Gom `series` (mỗi phần tử {date, counts}, chỉ có ngày THỰC SỰ có dữ liệu —
// không zero-fill ngày trống) thành các "cột" theo granularity, giữ nguyên
// thứ tự thời gian. Cắt bớt giữ `maxBars` cột GẦN NHẤT cho biểu đồ khỏi rối.
function resample(series, granularity, maxBars) {
  if (granularity === "day") {
    return series.slice(-maxBars).map((s) => ({ key: s.date, label: dayLabel(s.date), counts: s.counts }));
  }
  const keyFn = granularity === "week" ? (s) => isoWeekKey(s.date) : (s) => s.date.slice(0, 7);
  const byKey = new Map();
  for (const s of series) {
    const k = keyFn(s);
    const bucket = byKey.get(k) || {};
    for (const [field, v] of Object.entries(s.counts || {})) bucket[field] = (bucket[field] || 0) + v;
    byKey.set(k, bucket);
  }
  const keys = [...byKey.keys()].sort();
  const labelFn = granularity === "week" ? weekRangeLabel : monthLabel;
  return keys.slice(-maxBars).map((k) => ({ key: k, label: labelFn(k), counts: byKey.get(k) }));
}

// ── Bar chart SVG tối giản: N cột theo thời gian, mỗi cột 1 hoặc nhiều đoạn
// (stacked) theo `seriesSpec`. Đủ dùng cho 3 biểu đồ này — không tổng quát
// hoá quá mức thành 1 lib riêng.
//
// QUAN TRỌNG: SVG dùng viewBox 100 x CHART_H + preserveAspectRatio="none" để
// co giãn LẤP ĐẦY khung chứa (chart rộng ngắn tuỳ layout) — trục X và Y vì
// vậy bị kéo giãn KHÔNG ĐỀU NHAU (vd rộng gấp 4 lần cao). Hình chữ nhật (bar)
// không sao vì chỉ là khối màu, nhưng <text> BÊN TRONG svg đó sẽ bị kéo méo
// theo (chữ số/nhãn ngày bị "dẹt" — đúng lỗi thực tế gặp phải). Sửa bằng
// cách KHÔNG đặt text trong SVG nữa — render nhãn bằng HTML thường, đặt đè
// lên bằng position:absolute (trục X dùng %, trục Y dùng px vì chiều cao
// SVG khớp đúng 1:1 với CHART_H — xem CSS height của .qc-chart-svg).
const CHART_H = 184;
const BAR_GAP = 6;
const BAR_BOTTOM = CHART_H - 20; // đáy vẽ, chừa chỗ nhãn trục X
const PLOT_H = CHART_H - 32; // chừa thêm chỗ nhãn tổng số phía trên

function BarChart({ title, periods, seriesSpec }) {
  const [hover, setHover] = useState(null);
  const total = (p) => seriesSpec.reduce((s, x) => s + (p.counts[x.key] || 0), 0);
  const max = Math.max(1, ...periods.map(total));
  const showLegend = seriesSpec.length > 1;
  const n = periods.length;
  const barW = n ? Math.max(6, Math.min(40, (100 - BAR_GAP) / n - BAR_GAP)) : 0;
  // Chỉ dán nhãn trục X chọn lọc (≤ ~10 nhãn) — nhiều cột thì nhãn sẽ chồng chữ.
  const labelEvery = Math.max(1, Math.ceil(n / 10));

  // Tính trước hình học từng cột (vị trí %, các đoạn stack, đỉnh cột) — dùng
  // chung cho cả lớp SVG (bar) lẫn lớp HTML nhãn đè lên (không lệch nhau).
  const cols = periods.map((p, i) => {
    const xPct = i * (100 / n);
    const cxPct = xPct + (100 / n) / 2;
    let yTop = BAR_BOTTOM;
    const segs = seriesSpec.map((s) => {
      const v = p.counts[s.key] || 0;
      if (!v) return null;
      const h = (v / max) * PLOT_H;
      const y = yTop - h;
      yTop = y - 1.2; // khoảng cách giữa các đoạn stack
      return { key: s.key, x: xPct + (100 / n - barW) / 2, y, h: Math.max(0.6, h), color: s.color, label: s.label, v };
    }).filter(Boolean);
    return { key: p.key, label: p.label, cxPct, segs, topY: yTop, total: total(p) };
  });

  return (
    <div className="qc-chart-block">
      <div className="qc-chart-head">
        <span className="qc-chart-title">{title}</span>
        <span className="qc-chart-total">{fmt(periods.reduce((s, p) => s + total(p), 0))}</span>
      </div>
      {!n ? (
        <div className="muted center" style={{ padding: 24 }}>Chưa có dữ liệu trong khoảng này.</div>
      ) : (
        <div className="qc-chart-svg-wrap" style={{ height: CHART_H }}>
          <svg viewBox={`0 0 100 ${CHART_H}`} preserveAspectRatio="none" className="qc-chart-svg">
            {[0, 0.5, 1].map((f) => (
              <line key={f} x1={0} x2={100} y1={12 + (CHART_H - 32) * f} y2={12 + (CHART_H - 32) * f}
                stroke="var(--border)" strokeWidth={0.3} />
            ))}
            {cols.map((c) => c.segs.map((seg) => (
              <rect key={seg.key} x={seg.x} y={seg.y} width={barW} height={seg.h} rx={0.8} fill={seg.color}
                onMouseEnter={(e) => {
                  const box = e.currentTarget.ownerSVGElement.getBoundingClientRect();
                  setHover({
                    label: `${c.label} · ${seg.label}`, value: seg.v, color: seg.color,
                    cx: box.left + (c.cxPct / 100) * box.width,
                    cy: box.top + (seg.y / CHART_H) * box.height,
                  });
                }}
                onMouseLeave={() => setHover(null)} />
            )))}
          </svg>
          {/* Lớp nhãn HTML đè lên SVG — KHÔNG bị méo vì không đi qua transform
              co giãn không đều của viewBox (xem ghi chú trên CHART_H). Dùng
              fmtCompact (rút gọn "12,3k"/"4,5tr") vì chỗ trong cột/đoạn hẹp
              — số chính xác đầy đủ vẫn có khi hover (tooltip) hoặc ở khối
              KPI/hero bên trên (fmt, không rút gọn). */}
          <div className="qc-chart-labels">
            {cols.map((c, i) => (
              <React.Fragment key={c.key}>
                {/* Nhãn TỪNG ĐOẠN màu (loại) — chỉ hiện khi đoạn đủ cao để
                    không tràn/chồng chữ ra ngoài. */}
                {c.segs.filter((seg) => seg.h >= 16).map((seg) => (
                  <span key={seg.key} className="qc-chart-seg-label"
                    style={{ left: `${c.cxPct}%`, top: seg.y + seg.h / 2 }}>
                    {fmtCompact(seg.v)}
                  </span>
                ))}
                {c.total > 0 && (
                  <span className="qc-chart-bar-total" style={{ left: `${c.cxPct}%`, top: c.topY - 1 }}>
                    {fmtCompact(c.total)}
                  </span>
                )}
                {i % labelEvery === 0 && (
                  <span className="qc-chart-x-label" style={{ left: `${c.cxPct}%`, top: CHART_H - 16 }}>
                    {c.label}
                  </span>
                )}
              </React.Fragment>
            ))}
          </div>
          {hover && (
            <div className="qc-chart-tooltip" style={{ left: hover.cx, top: hover.cy }}>
              <i style={{ background: hover.color }} />{hover.label}: <b>{fmt(hover.value)}</b>
            </div>
          )}
        </div>
      )}
      {showLegend && (
        <div className="qc-chart-legend">
          {seriesSpec.map((s) => (
            <span key={s.key}><i style={{ background: s.color }} /> {s.label}</span>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Khối "Tổng chất lượng QC" — số + % đạt, hero heuristic (1 chỉ số quan
// trọng không cần vẽ chart, xem skill dataviz §choosing-a-form). ─────────────
function QcQualityHero({ counts }) {
  const scanned = counts?.scanned || 0;
  const dat = (counts?.pass || 0) + (counts?.warn || 0);
  const fail = counts?.fail || 0;
  const datPct = pct(dat, scanned);
  return (
    <div className="qc-hero">
      <div className="qc-hero-pct-wrap">
        <span className="qc-hero-pct">{scanned ? `${datPct}%` : "—"}</span>
        <span className="qc-hero-pct-label">tỉ lệ đạt QC</span>
      </div>
      <div className="qc-hero-body">
        <div className="qc-hero-bar">
          <div className="qc-hero-bar-fill" style={{ width: `${datPct}%` }} />
        </div>
        <div className="qc-hero-nums">
          <span><b>{fmt(scanned)}</b> đã quét</span>
          <span className="qc-hero-ok"><i />Đạt <b>{fmt(dat)}</b> ({datPct}%)</span>
          <span className="qc-hero-err"><i />Không đạt <b>{fmt(fail)}</b> ({pct(fail, scanned)}%)</span>
        </div>
      </div>
    </div>
  );
}

// Dải KPI phụ (OCR/cắt) — bổ sung cho hero, không lặp lại pass/warn/fail/scanned.
function OcrKpiStrip({ counts }) {
  const TILES = [
    ["ocr_done", "Đã cắt (item)"], ["cuts_created", "File đã cắt"],
    ["no_gcn", "Không thấy GCN"], ["no_file", "Không thấy file"], ["error", "Lỗi"],
  ];
  return (
    <div className="qc-kpi-row">
      {TILES.map(([k, label]) => (
        <div key={k} className="qc-kpi-tile">
          <span className="qc-kpi-n">{fmt(counts?.[k])}</span>
          <span className="qc-kpi-label">{label}</span>
        </div>
      ))}
    </div>
  );
}

// ── Bảng "Theo Phường/Xã" — liệt kê MỌI kênh (kể cả kênh chưa có hoạt động
// nào) + xem nhanh danh sách file/OCR/file cắt của từng kênh ngay tại chỗ. ──
function WardTable({ range }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState("");
  const [sortBy, setSortBy] = useState("name");

  useEffect(() => {
    setLoading(true);
    getQcSyncStatsByConfig(range).then((d) => setRows(d.rows || []))
      .catch((e) => toastErr(e.message || e)).finally(() => setLoading(false));
  }, [range]);

  const sorted = useMemo(() => {
    const arr = [...rows];
    if (sortBy === "scanned") arr.sort((a, b) => (b.counts.scanned || 0) - (a.counts.scanned || 0));
    else arr.sort((a, b) => (a.ward_name || a.name).localeCompare(b.ward_name || b.name, "vi"));
    return arr;
  }, [rows, sortBy]);

  return (
    <div className="qc-ward-sec">
      <div className="qc-ward-head">
        <h4>Theo Phường/Xã</h4>
        <div className="seg-toggle sm">
          <button type="button" className={sortBy === "name" ? "active" : ""} onClick={() => setSortBy("name")}>Tên A→Z</button>
          <button type="button" className={sortBy === "scanned" ? "active" : ""} onClick={() => setSortBy("scanned")}>Quét nhiều nhất</button>
        </div>
      </div>
      <div className="tbl-dense qc-ward-tbl">
        <div className="file-row qc-ward-row qc-ward-rowhead">
          <span>Phường/Xã</span><span>Đã quét</span><span>Đạt</span><span>Không đạt</span>
          <span>Đã cắt</span><span>Không GCN</span><span>Lỗi</span><span />
        </div>
        {sorted.map((r) => {
          const c = r.counts || {};
          const isOpen = expanded === r.config_id;
          return (
            <React.Fragment key={r.config_id}>
              <div className="file-row qc-ward-row">
                <span className="fr-name">{r.ward_name || r.name}</span>
                <span className="fr-meta">{fmt(c.scanned)}</span>
                <span className="fr-meta qc-t-ok">{fmt((c.pass || 0) + (c.warn || 0))}</span>
                <span className="fr-meta qc-t-err">{fmt(c.fail)}</span>
                <span className="fr-meta">{fmt(c.ocr_done)}</span>
                <span className="fr-meta">{fmt(c.no_gcn)}</span>
                <span className="fr-meta">{fmt(c.error)}</span>
                <span className="s3-actions">
                  <button className="ghost xs" onClick={() => setExpanded(isOpen ? "" : r.config_id)}>
                    {isOpen ? "Ẩn" : "Xem"}
                  </button>
                </span>
              </div>
              {isOpen && <WardItemsPanel configId={r.config_id} />}
            </React.Fragment>
          );
        })}
        {!sorted.length && (
          <div className="muted center" style={{ padding: 16 }}>{loading ? "Đang tải…" : "Chưa có kênh nào."}</div>
        )}
      </div>
    </div>
  );
}

// Danh sách file gần đây của 1 kênh — CHỈ XEM (không có Chạy lại/Xóa, những
// thao tác đó nằm ở trang quản trị "QC Sync" admin-only).
const WARD_ITEMS_PAGE_SIZE = 15;

function WardItemsPanel({ configId }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [ocrItem, setOcrItem] = useState(null);
  const [page, setPage] = useState(1);

  useEffect(() => {
    setLoading(true);
    // processedOnly + sortBy "finished_at": panel này chỉ để XEM kết quả đã
    // xử lý xong (đúng ý "Theo Phường/Xã") — khác bảng admin "File gần đây"
    // (QcSync.jsx) vốn mặc định thấy CẢ hàng chờ để debug tiến độ. Sort mặc
    // định trước đây là created_at → luôn nổi lên file MỚI NHẤT ĐƯỢC LIỆT KÊ,
    // mà file mới liệt kê thường còn "queued" (chưa tới lượt xử lý) — đúng
    // bug thực tế "toàn ra file queued".
    getQcSyncItems({ configId, processedOnly: true, sortBy: "finished_at", page, pageSize: WARD_ITEMS_PAGE_SIZE })
      .then((d) => setItems(d.items || []))
      .catch(() => setItems([])).finally(() => setLoading(false));
  }, [configId, page]);

  return (
    <div className="qc-ward-detail">
      {loading ? (
        <div className="muted small" style={{ padding: 8 }}>Đang tải…</div>
      ) : items.length ? (
        <div className="tbl-dense qc-ward-items-tbl">
          <div className="file-row qc-ward-item-row qc-ward-item-head">
            <span>S3 key</span><span>Verdict</span><span>Trạng thái</span><span>File đã cắt</span><span>OCR</span><span>Lúc</span>
          </div>
          {items.map((it) => (
            <div className="file-row qc-ward-item-row" key={it.id}>
              <a className="fr-name" href={qcSyncSourcePdfUrl(it.id)} target="_blank" rel="noopener noreferrer"
                title={`Xem PDF nguồn: ${it.s3_key}`}>{it.s3_key}</a>
              <span className="fr-meta">
                {it.qc?.verdict && (
                  <><span className={`dot ${VERDICT_CLASS[it.qc.verdict] || "dot-unknown"}`} />{" "}
                  {VERDICT_LABEL[it.qc.verdict] || it.qc.verdict}</>
                )}
              </span>
              <span className="fr-meta">{it.status}</span>
              <span className="qc-cuts-cell">
                {(it.ocr?.cuts || []).length
                  ? it.ocr.cuts.map((cut) => (
                      <a key={cut.index} href={qcSyncCutPdfUrl(it.id, cut.index)} target="_blank"
                        rel="noopener noreferrer" title={`Xem file đã cắt: ${cut.name}`}>{cut.name}</a>
                    ))
                  : <span className="muted small">—</span>}
              </span>
              <span>
                {(it.ocr?.records || []).length
                  ? <button className="ghost xs" onClick={() => setOcrItem(it)}>Xem OCR</button>
                  : <span className="muted small">—</span>}
              </span>
              <span className="fr-meta">{fmtDate(it.finished_at || it.created_at)}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="muted center" style={{ padding: 16 }}>
          {page > 1 ? "Hết file ở trang này." : "Chưa có file nào đã xử lý xong."}
        </div>
      )}
      {!loading && (items.length || page > 1) && (
        <div className="row" style={{ gap: 8, marginTop: 8 }}>
          <button className="ghost xs" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>← Trang trước</button>
          <span className="muted small">Trang {page}</span>
          <button className="ghost xs" disabled={items.length < WARD_ITEMS_PAGE_SIZE}
            onClick={() => setPage((p) => p + 1)}>Trang sau →</button>
        </div>
      )}
      {ocrItem && (
        <Modal title={`Nội dung OCR — ${ocrItem.s3_key}`} onClose={() => setOcrItem(null)} wide>
          <pre className="qc-ocr-json">{JSON.stringify(ocrItem.ocr?.records ?? {}, null, 2)}</pre>
        </Modal>
      )}
    </div>
  );
}

export default function QcSyncStats() {
  const [configs, setConfigs] = useState([]);
  const [configId, setConfigId] = useState("");
  const [range, setRange] = useState("week");
  const [series, setSeries] = useState([]);
  const [totalCounts, setTotalCounts] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    getQcSyncConfigs().then((d) => setConfigs(d.configs || [])).catch(() => {});
  }, []);

  const rangeDef = RANGES.find(([k]) => k === range);

  useEffect(() => {
    setLoading(true);
    getQcSyncStats({ configId: configId || undefined, range })
      .then((d) => setTotalCounts(d.counts || {}))
      .catch((e) => toastErr(e.message || e));
    if (range === "all") { setSeries([]); setLoading(false); return; }
    getQcSyncStatsSeries({ configId: configId || undefined, days: rangeDef[2] })
      .then((d) => setSeries(d.series || []))
      .catch((e) => toastErr(e.message || e))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [configId, range]);

  const periods = useMemo(() => {
    if (range === "all") return [];
    const maxBars = range === "day" ? 30 : range === "week" ? 13 : 12;
    return resample(series, range, maxBars);
  }, [series, range]);

  return (
    <div className="export-sec qc-stats-sec">
      <div className="export-head">
        <h3><Icon name="barChart" size={16} /> QC Sync <span className="muted">(kiểm chất lượng · OCR · cắt GCN)</span></h3>
        <div className="et-filters">
          <select value={configId} onChange={(e) => setConfigId(e.target.value)}>
            <option value="">Tất cả kênh</option>
            {configs.map((c) => (
              <option key={c.id} value={c.id}>{c.ward_name || c.name}</option>
            ))}
          </select>
          <div className="seg-toggle sm" style={{marginTop: 0, flexShrink: 0}}>
            {RANGES.map(([k, label]) => (
              <button key={k} type="button" className={range === k ? "active" : ""}
                onClick={() => setRange(k)}>{label}</button>
            ))}
          </div>
        </div>
      </div>

      <QcQualityHero counts={totalCounts} />
      <OcrKpiStrip counts={totalCounts} />

      {range !== "all" && (
        <div className="qc-chart-grid">
          <BarChart title="Kiểm chất lượng (QC)" periods={periods} seriesSpec={QC_SERIES} />
          <BarChart title="Kết quả OCR" periods={periods} seriesSpec={OCR_SERIES} />
          <BarChart title="Số file đã cắt" periods={periods} seriesSpec={CUTS_SERIES} />
        </div>
      )}
      {loading && <div className="muted small" style={{ padding: "6px 2px" }}>Đang tải…</div>}

      {!configId && <WardTable range={range} />}
    </div>
  );
}
