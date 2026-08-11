import React, { useEffect, useMemo, useState } from "react";
import Icon from "./Icon.jsx";
import { getQcSyncConfigs, getQcSyncStats, getQcSyncStatsSeries } from "../api.js";
import { toastErr } from "../toast.js";

// Thống kê pipeline QC Sync (F-16, xem docs/algorithm.md §9) trên trang Tổng
// quan — viewer trở lên xem được, khác trang quản trị "QC Sync" (admin-only).
// Không có chart library trong repo (chỉ react/vite) — tự vẽ bar chart SVG
// nhẹ, theo skill dataviz: 1 trục, màu status-palette có sẵn (--ok/--warn/
// --err), legend cho ≥2 chuỗi, hover tooltip per-mark, "Tổng" = KPI tile chứ
// không phải bar chart cho 1 giá trị duy nhất.

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

const fmt = (n) => (n || 0).toLocaleString("vi-VN");

function isoWeekKey(dateStr) {
  const d = new Date(`${dateStr}T00:00:00Z`);
  const dow = (d.getUTCDay() + 6) % 7; // 0=Thứ 2
  d.setUTCDate(d.getUTCDate() - dow); // lùi về Thứ 2 đầu tuần
  return d.toISOString().slice(0, 10);
}
function periodLabel(key, granularity) {
  if (granularity === "month") {
    const [y, m] = key.split("-");
    return `Thg ${Number(m)}/${y}`;
  }
  const [, m, d] = key.split("-");
  return `${d}/${m}`;
}

// Gom `series` (mỗi phần tử {date, counts}, chỉ có ngày THỰC SỰ có dữ liệu —
// không zero-fill ngày trống) thành các "cột" theo granularity, giữ nguyên
// thứ tự thời gian. Cắt bớt giữ `maxBars` cột GẦN NHẤT cho biểu đồ khỏi rối.
function resample(series, granularity, maxBars) {
  if (granularity === "day") {
    return series.slice(-maxBars).map((s) => ({ key: s.date, label: periodLabel(s.date, "day"), counts: s.counts }));
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
  return keys.slice(-maxBars).map((k) => ({ key: k, label: periodLabel(k, granularity), counts: byKey.get(k) }));
}

// ── Bar chart SVG tối giản: N cột theo thời gian, mỗi cột 1 hoặc nhiều đoạn
// (stacked) theo `seriesSpec`. Đủ dùng cho 3 biểu đồ này — không tổng quát
// hoá quá mức thành 1 thư viện chart riêng.
const CHART_H = 176;
const BAR_GAP = 6;

function BarChart({ title, periods, seriesSpec }) {
  const [hover, setHover] = useState(null); // {x, y, label, value, color}
  const total = (p) => seriesSpec.reduce((s, x) => s + (p.counts[x.key] || 0), 0);
  const max = Math.max(1, ...periods.map(total));
  const showLegend = seriesSpec.length > 1;
  const n = periods.length;
  const barW = n ? Math.max(6, Math.min(40, (100 - BAR_GAP) / n - BAR_GAP)) : 0;
  // Chỉ dán nhãn trục X chọn lọc (≤ ~10 nhãn) — nhiều cột thì nhãn sẽ chồng chữ.
  const labelEvery = Math.max(1, Math.ceil(n / 10));

  return (
    <div className="qc-chart-block">
      <div className="qc-chart-head">
        <span className="qc-chart-title">{title}</span>
        <span className="qc-chart-total">{fmt(periods.reduce((s, p) => s + total(p), 0))}</span>
      </div>
      {!n ? (
        <div className="muted center" style={{ padding: 24 }}>Chưa có dữ liệu trong khoảng này.</div>
      ) : (
        <div className="qc-chart-svg-wrap">
          <svg viewBox={`0 0 100 ${CHART_H}`} preserveAspectRatio="none" className="qc-chart-svg">
            {/* Gridline mờ tại 0%/50%/100% chiều cao vẽ */}
            {[0, 0.5, 1].map((f) => (
              <line key={f} x1={0} x2={100} y1={4 + (CHART_H - 24) * f} y2={4 + (CHART_H - 24) * f}
                stroke="var(--border)" strokeWidth={0.3} />
            ))}
            {periods.map((p, i) => {
              const x = i * (100 / n) + (100 / n - barW) / 2;
              let yTop = CHART_H - 20; // đáy vẽ, chừa chỗ nhãn trục X
              const plotH = CHART_H - 24;
              return (
                <g key={p.key}>
                  {seriesSpec.map((s) => {
                    const v = p.counts[s.key] || 0;
                    if (!v) return null;
                    const h = (v / max) * plotH;
                    yTop -= h;
                    const y = yTop;
                    yTop -= 1.2; // khoảng cách 2px giữa các đoạn stack (đơn vị viewBox)
                    return (
                      <rect key={s.key} x={x} y={y} width={barW} height={Math.max(0.6, h)}
                        rx={0.8} fill={s.color}
                        onMouseEnter={(e) => setHover({
                          label: `${p.label} · ${s.label}`, value: v, color: s.color,
                          cx: e.currentTarget.ownerSVGElement.getBoundingClientRect().left + (x + barW / 2) / 100
                            * e.currentTarget.ownerSVGElement.getBoundingClientRect().width,
                          cy: e.currentTarget.ownerSVGElement.getBoundingClientRect().top + (y / CHART_H)
                            * e.currentTarget.ownerSVGElement.getBoundingClientRect().height,
                        })}
                        onMouseLeave={() => setHover(null)} />
                    );
                  })}
                  {i % labelEvery === 0 && (
                    <text x={i * (100 / n) + (100 / n) / 2} y={CHART_H - 6} textAnchor="middle"
                      fontSize={4.6} fill="var(--text-3)">{p.label}</text>
                  )}
                </g>
              );
            })}
          </svg>
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

function KpiRow({ counts }) {
  const TILES = [
    ["scanned", "Đã quét"], ["pass", "Đạt"], ["warn", "Đạt (cảnh báo)"], ["fail", "Không đạt"],
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
    if (range === "all") {
      getQcSyncStats({ configId: configId || undefined, range: "all" })
        .then((d) => setTotalCounts(d.counts || {}))
        .catch((e) => toastErr(e.message || e))
        .finally(() => setLoading(false));
      return;
    }
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
          <div className="seg-toggle sm">
            {RANGES.map(([k, label]) => (
              <button key={k} type="button" className={range === k ? "active" : ""}
                onClick={() => setRange(k)}>{label}</button>
            ))}
          </div>
        </div>
      </div>

      {loading && <div className="muted small" style={{ padding: "0 4px 8px" }}>Đang tải…</div>}

      {range === "all" ? (
        <KpiRow counts={totalCounts} />
      ) : (
        <div className="qc-chart-grid">
          <BarChart title="Kiểm chất lượng (QC)" periods={periods} seriesSpec={QC_SERIES} />
          <BarChart title="Kết quả OCR" periods={periods} seriesSpec={OCR_SERIES} />
          <BarChart title="Số file đã cắt" periods={periods} seriesSpec={CUTS_SERIES} />
        </div>
      )}
    </div>
  );
}
