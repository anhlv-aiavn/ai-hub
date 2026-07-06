import React, { useEffect, useRef, useState } from "react";
import Icon from "./Icon.jsx";
import GcnPdf from "./GcnPdf.jsx";
import Pager from "./Pager.jsx";
import {
  listRows, listBatches, downloadCsv, getStats, getBranches,
  createExportJob, getExportJob, downloadExportJob,
} from "../api.js";
import { subscribeEvents } from "../events.js";
import { toastOk, toastErr } from "../toast.js";

const ROWS_PAGE_SIZE = 50;
const REVIEW = { "": "Mọi hậu kiểm", reviewed: "Đã duyệt", needs_review: "Cần xem", unreviewed: "Chưa kiểm" };
const FILE_COLS = new Set(["Tệp gốc", "Tệp cắt"]);

const STATUS_SEGS = [
  { key: "done", label: "Xong", cls: "ok" },
  { key: "processing", label: "Đang xử lý", cls: "info" },
  { key: "queued", label: "Chờ", cls: "muted" },
  { key: "error", label: "Lỗi", cls: "danger" },
  { key: "skip", label: "Bỏ qua", cls: "warn" },
];
const REVIEW_SEGS = [
  { key: "reviewed", label: "Đã duyệt", cls: "ok" },
  { key: "needs_review", label: "Cần xem", cls: "warn" },
  { key: "unreviewed", label: "Chưa kiểm", cls: "muted" },
];

const fmt = (n) => (n || 0).toLocaleString("vi-VN");
const pct = (n, total) => (total ? Math.round((n / total) * 100) : 0);

function SegBar({ title, segs, data, unit = "hồ sơ" }) {
  const items = segs.map((x) => ({ ...x, n: data[x.key] || 0 }));
  const total = items.reduce((a, b) => a + b.n, 0);
  const denom = total || 1;
  return (
    <div className="seg-block">
      <div className="seg-head">
        <span className="seg-title">{title}</span>
        <span className="seg-total">{fmt(total)} <small>{unit}</small></span>
      </div>
      <div className="seg-bar" role="img"
        aria-label={items.map((x) => `${x.label}: ${x.n} (${pct(x.n, denom)}%)`).join(", ")}>
        {items.filter((x) => x.n > 0).map((x) => (
          <div key={x.key} className={`seg seg-${x.cls}`} style={{ width: `${(x.n / denom) * 100}%` }}
            title={`${x.label}: ${x.n} (${pct(x.n, denom)}%)`} />
        ))}
      </div>
      <div className="seg-legend">
        {items.map((x) => (
          <span key={x.key} className="seg-leg">
            <i className={`seg-dot seg-${x.cls}`} /> {x.label}
            <b>{fmt(x.n)}</b> <span className="seg-pct">{pct(x.n, denom)}%</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function BranchTable({ rows }) {
  if (!rows.length) return null;
  // Xếp hạng theo SỐ ĐÃ DUYỆT giảm dần (tie: nhiều GCN hơn trước).
  const ranked = [...rows].sort((a, b) => (b.reviewed - a.reviewed) || (b.gcns - a.gcns));

  return (
    <div className="branch-stats">
      <div className="seg-title">Theo chi nhánh · xếp hạng theo số đã duyệt</div>
      <div className="et-scroll bt-scroll">
        <table className="et-grid">
          <thead>
            <tr>
              <th>#</th><th>Chi nhánh</th><th>Hồ sơ</th><th>GCN</th>
              <th>Tiến độ xử lý AI</th><th>Đã duyệt</th>
            </tr>
          </thead>
          <tbody>
            {ranked.map((r, i) => {
              const donePct = pct(r.done, r.files);
              const revPct = pct(r.reviewed, r.files);
              return (
                <tr key={r.branch || `__${i}`}>
                  <td className="bt-rank">{i + 1}</td>
                  <td className="bt-name">{r.branch || "(chưa gán)"}</td>
                  <td>{fmt(r.files)}</td>
                  <td>{fmt(r.gcns)}</td>
                  <td>
                    <div className="bt-prog">
                      <div className="bt-bar"><div className="bt-fill" style={{ width: `${donePct}%` }} /></div>
                      <span className="bt-pct"><b>{fmt(r.done)}</b> <span className="muted">/{fmt(r.files)} · {donePct}%</span></span>
                    </div>
                  </td>
                  <td>
                    <div className="bt-prog">
                      <div className="bt-bar"><div className="bt-fill rev" style={{ width: `${revPct}%` }} /></div>
                      <span className="bt-pct"><b>{fmt(r.reviewed)}</b> <span className="muted">· {revPct}%</span></span>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// Thống kê: KPI + breakdown trạng thái/hậu kiểm + cảnh báo, kèm xuất CSV/bảng phẳng (FME).
export default function ExportView({ user }) {
  const isAdmin = user?.role === "admin";
  const canExportJob = user?.role !== "viewer";
  const [batches, setBatches] = useState([]);
  const [branches, setBranches] = useState([]);
  const [batchId, setBatchId] = useState("");
  const [branch, setBranch] = useState("");
  const [review, setReview] = useState("");
  const [stats, setStats] = useState(null);
  const [cols, setCols] = useState([]);
  const [rows, setRows] = useState([]);
  const [rowsTotal, setRowsTotal] = useState(0);
  const [rowsPage, setRowsPage] = useState(1);
  const [rowsTotalPages, setRowsTotalPages] = useState(1);
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState(null); // {gcnId, cutIndex, title}
  const [pvPage, setPvPage] = useState(1);
  const [job, setJob] = useState(null); // {job_id, status, row_count, error}
  const pollRef = useRef(null);
  const refreshRef = useRef(() => {});

  function openPreview(p) { setPvPage(1); setPreview(p); }

  async function refresh(p = rowsPage) {
    setLoading(true);
    try {
      const [d, st] = await Promise.all([
        listRows({ batchId: batchId || undefined, branch: branch || undefined, review: review || undefined,
                   page: p, pageSize: ROWS_PAGE_SIZE }),
        getStats({ batchId: batchId || undefined, branch: branch || undefined }),
      ]);
      setCols(d.columns || []);
      setRows(d.rows || []);
      setRowsTotal(d.total || 0);
      setRowsTotalPages(d.total_pages || 1);
      setStats(st);
    } catch (e) { toastErr(e.message || e); } finally { setLoading(false); }
  }
  useEffect(() => { refreshRef.current = refresh; });

  useEffect(() => {
    listBatches().then((d) => setBatches(d.batches || [])).catch(() => {});
    if (isAdmin) getBranches().then((d) => setBranches(d.branches || [])).catch(() => {});
  }, [isAdmin]);
  // Đổi bộ lọc → về trang 1 (không dùng state `rowsPage` cũ để tránh closure lệch nhịp).
  useEffect(() => { setRowsPage(1); refresh(1); /* eslint-disable-next-line */ }, [batchId, branch, review]);
  useEffect(() => {
    let t = null;
    const un = subscribeEvents(() => { clearTimeout(t); t = setTimeout(() => refreshRef.current(), 800); });
    return () => { un(); clearTimeout(t); };
  }, []);

  function goToRowsPage(p) { setRowsPage(p); refresh(p); }

  async function csv() {
    try {
      await downloadCsv({ batchId: batchId || undefined, branch: branch || undefined, review: review || undefined });
      toastOk("Đã tải CSV");
    } catch (e) { toastErr(e.message || e); }
  }

  // Xuất nền: không cap dòng, không chặn request — worker đọc Mongo qua cursor.
  useEffect(() => () => clearInterval(pollRef.current), []);
  async function startExportJob() {
    clearInterval(pollRef.current);
    try {
      const { job_id } = await createExportJob({
        batchId: batchId || undefined, branch: branch || undefined, review: review || undefined,
      });
      setJob({ job_id, status: "queued" });
      pollRef.current = setInterval(async () => {
        try {
          const j = await getExportJob(job_id);
          setJob(j);
          if (j.status === "done" || j.status === "error") clearInterval(pollRef.current);
        } catch { clearInterval(pollRef.current); }
      }, 2000);
    } catch (e) { toastErr(e.message || e); }
  }

  function cell(col, r) {
    if (col === "Tệp gốc") {
      if (!r._gcn_id) return "—";
      return (
        <button className="link-btn" onClick={() => openPreview({ gcnId: r._gcn_id, cutIndex: null, title: r["Tệp gốc"] })}>
          <Icon name="fileText" size={13} /> {r["Tệp gốc"] || "Xem"}
        </button>
      );
    }
    if (col === "Tệp cắt") {
      if (r._cut_index === null || r._cut_index === undefined) return <span className="muted">—</span>;
      return (
        <button className="link-btn" onClick={() => openPreview({ gcnId: r._gcn_id, cutIndex: r._cut_index, title: r["Tệp cắt"] })}>
          <Icon name="scissors" size={13} /> {r["Tệp cắt"] || "Xem cắt"}
        </button>
      );
    }
    const v = r[col];
    return v == null ? "" : String(v);
  }

  const s = stats || {};
  const st = s.by_status || {};

  return (
    <div className="panel export-view">
      <div className="et-toolbar">
        <h2>Tổng quan</h2>
        <div className="et-filters">
          {isAdmin && (
            <select value={branch} onChange={(e) => setBranch(e.target.value)}>
              <option value="">Tất cả chi nhánh</option>
              {branches.map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
          )}
          <select value={batchId} onChange={(e) => setBatchId(e.target.value)}>
            <option value="">Tất cả đợt</option>
            {batches.map((b) => <option key={b.batch_id} value={b.batch_id}>{b.name} · {b.file_count} hồ sơ</option>)}
          </select>
          <button className="ghost sm" onClick={() => refresh()}><Icon name="refresh" size={14} /> Làm mới</button>
        </div>
      </div>

      <div className="seg-row">
        <SegBar title="Trạng thái xử lý" segs={STATUS_SEGS} data={st} />
        <SegBar title="Hậu kiểm" segs={REVIEW_SEGS} data={s.by_review || {}} />
      </div>

      {isAdmin && <BranchTable rows={s.by_branch || []} />}

      <div className="export-sec">
        <div className="export-head">
          <h3>Xuất dữ liệu <span className="muted">(CSV · mỗi thửa một dòng)</span></h3>
          <div className="et-filters">
            <select value={review} onChange={(e) => setReview(e.target.value)}>
              {Object.entries(REVIEW).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <span className="muted ev-count">{fmt(rowsTotal)} dòng</span>
            <button className="primary sm" onClick={csv}><Icon name="download" size={14} /> Tải CSV</button>
            {canExportJob && (
              <button className="ghost sm" disabled={job && job.status !== "done" && job.status !== "error"}
                onClick={startExportJob} title="Không giới hạn số dòng — chạy nền, không cap 5000 dòng như Tải CSV">
                <Icon name="upload" size={14} /> Xuất nền
              </button>
            )}
          </div>
        </div>

        {job && (
          <div className="export-job-status">
            {job.status === "queued" && <>Đang chờ worker…</>}
            {job.status === "processing" && <>Đang xuất… {job.row_count ? `(${job.row_count} dòng)` : ""}</>}
            {job.status === "error" && <span className="error">Lỗi xuất: {job.error}</span>}
            {job.status === "done" && (
              <>
                <Icon name="checkCircle" size={14} />
                Xong · {job.row_count} dòng
                <button className="ghost xs" onClick={() => downloadExportJob(job.job_id)}>
                  <Icon name="download" size={12} /> Tải xuống
                </button>
              </>
            )}
          </div>
        )}

        <div className="et-scroll">
          <table className="et-grid flat-grid">
            <thead>
              <tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>{cols.map((c) => (
                  <td key={c} title={FILE_COLS.has(c) ? "" : (r[c] == null ? "" : String(r[c]))}>{cell(c, r)}</td>
                ))}</tr>
              ))}
              {!rows.length && (
                <tr><td colSpan={cols.length || 1} className="muted center">
                  {loading ? "Đang tải…" : "Chưa có dòng nào khớp bộ lọc."}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>

        <Pager page={rowsPage} totalPages={rowsTotalPages} total={rowsTotal} unit="dòng" onChange={goToRowsPage} />
      </div>

      {preview && (
        <div className="modal-overlay" onClick={() => setPreview(null)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <b>{preview.cutIndex != null ? "File cắt" : "File gốc"} · {preview.title}</b>
              <button className="icon-btn" onClick={() => setPreview(null)} aria-label="Đóng"><Icon name="x" size={16} /></button>
            </div>
            <div className="modal-body">
              <GcnPdf gcnId={preview.gcnId} cutIndex={preview.cutIndex} page={pvPage} onPageChange={setPvPage} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
