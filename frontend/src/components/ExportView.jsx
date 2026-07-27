import React, { useEffect, useMemo, useRef, useState } from "react";
import Icon from "./Icon.jsx";
import GcnPdf from "./GcnPdf.jsx";
import Pager from "./Pager.jsx";
import {
  listRows, listBatches, downloadCsv, getStats,
  createExportJob, getExportJob, downloadExportJob, friendlyError,
} from "../api.js";
import { subscribeEvents } from "../events.js";
import { toastOk, toastErr } from "../toast.js";
import { STATUS_LABEL, REVIEW_LABEL } from "./ExtractTable.jsx";

const ROWS_PAGE_SIZE = 50;
const REVIEW = { "": "Mọi hậu kiểm", reviewed: "Đã duyệt", needs_review: "Không duyệt", unreviewed: "Chưa kiểm" };
const FILE_COLS = new Set(["Tệp gốc", "Tệp cắt"]);

const STATUS_SEGS = [
  { key: "done", label: "Xong", cls: "ok" },
  { key: "processing", label: "Đang xử lý", cls: "info" },
  { key: "queued", label: "Chờ", cls: "muted" },
  { key: "error", label: "Lỗi", cls: "danger" },
  { key: "no_gcn", label: "Không có giấy", cls: "warn" },
  { key: "skip", label: "Bỏ qua", cls: "warn" },
];
const REVIEW_SEGS = [
  { key: "reviewed", label: "Đã duyệt", cls: "ok" },
  { key: "needs_review", label: "Không duyệt", cls: "danger" },
  { key: "unreviewed", label: "Chưa kiểm", cls: "muted" },
];

const fmt = (n) => (n || 0).toLocaleString("vi-VN");
// Làm tròn đến 2 chữ số thập phân — làm tròn số nguyên rồi cộng các phần có thể lệch tổng (vd 99%/101%).
const pct = (n, total) => (total ? ((n / total) * 100).toFixed(2) : "0.00");

function SegBar({ title, segs, data, unit = "hồ sơ", pendingKeys }) {
  const items = segs.map((x) => ({ ...x, n: data[x.key] || 0 }));
  const total = items.reduce((a, b) => a + b.n, 0);
  const denom = total || 1;
  // pendingKeys: các trạng thái coi là "chưa xử lý xong" (vd Chờ, Đang xử lý).
  // Đã xử lý = tổng - các trạng thái đó (khớp cách tính ở ProgressMini/_rollup).
  const pending = pendingKeys ? pendingKeys.reduce((a, k) => a + (data[k] || 0), 0) : null;
  const processed = pendingKeys ? total - pending : null;
  return (
    <div className="seg-block">
      <div className="seg-head">
        <span className="seg-title">{title}</span>
        <span className="seg-total">
          {pendingKeys ? `${fmt(processed)}/${fmt(total)}` : fmt(total)} <small>{unit}</small>
        </span>
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

const REVIEWER_DAYS = [
  { key: 1, label: "Hôm nay" },
  { key: 7, label: "7 ngày" },
  { key: 30, label: "30 ngày" },
  { key: 0, label: "Tất cả" },
  { key: "custom", label: "Khoảng ngày…" },
];

function ReviewerTable({ rows, days, onDaysChange, rangeFrom, rangeTo, onRangeChange }) {
  const ranked = [...rows].sort((a, b) => (b.reviewed - a.reviewed) || (b.rejected - a.rejected));

  return (
    <div className="branch-stats">
      <div className="rt-head">
        <div className="seg-title">Theo người hậu kiểm · xếp hạng theo số đã duyệt</div>
        <div className="seg-toggle sm">
          {REVIEWER_DAYS.map((o) => (
            <button key={o.key} type="button" className={days === o.key ? "active" : ""}
              onClick={() => onDaysChange(o.key)}>{o.label}</button>
          ))}
        </div>
        {days === "custom" && (
          <div className="rt-range">
            <input type="date" value={rangeFrom} max={rangeTo || undefined}
              onChange={(e) => onRangeChange(e.target.value, rangeTo)} />
            <span className="muted">–</span>
            <input type="date" value={rangeTo} min={rangeFrom || undefined}
              onChange={(e) => onRangeChange(rangeFrom, e.target.value)} />
          </div>
        )}
      </div>
      {ranked.length ? (
        <div className="et-scroll bt-scroll">
          <table className="et-grid">
            <thead>
              <tr><th>#</th><th>Người hậu kiểm</th><th>Đã duyệt</th><th>Không duyệt</th><th>Tổng</th></tr>
            </thead>
            <tbody>
              {ranked.map((r, i) => (
                <tr key={r.reviewer || `__${i}`}>
                  <td className="bt-rank">{i + 1}</td>
                  <td className="bt-name">{r.reviewer}</td>
                  <td>{fmt(r.reviewed)}</td>
                  <td>{fmt(r.rejected)}</td>
                  <td>{fmt(r.reviewed + r.rejected)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="muted center" style={{ padding: 16 }}>
          Chưa có ai duyệt/không duyệt hồ sơ nào trong khoảng thời gian này.
        </div>
      )}
    </div>
  );
}

// Thống kê: KPI + breakdown trạng thái/hậu kiểm + cảnh báo, kèm xuất CSV/bảng phẳng (FME).
export default function ExportView({ user }) {
  const isAdmin = user?.role === "admin";
  const canExportJob = user?.role !== "viewer";
  // Bảng "Theo người hậu kiểm": admin xem toàn hệ thống, operator xem được
  // (nhưng server luôn giới hạn theo các lô họ được gán — xem scoped_batch_ids
  // ở backend — nên danh sách trả về chỉ gồm tài khoản cùng phạm vi lô). Viewer
  // không có quyền hậu kiểm nên không cần thấy bảng này.
  const canViewReviewerTable = isAdmin || user?.role === "operator";
  const [batches, setBatches] = useState([]);
  const [batchId, setBatchId] = useState("");
  const [review, setReview] = useState("");
  const [reviewerDays, setReviewerDays] = useState(0); // 0 = toàn thời gian, "custom" = dùng reviewerFrom/To
  const [reviewerFrom, setReviewerFrom] = useState("");
  const [reviewerTo, setReviewerTo] = useState("");
  const [stats, setStats] = useState(null);
  const [cols, setCols] = useState([]);
  const [rows, setRows] = useState([]);
  const [rowsTotal, setRowsTotal] = useState(0); // ĐẾM HỒ SƠ (doc), không phải dòng
  const [docOffset, setDocOffset] = useState(0); // số hồ sơ trước trang này → STT liên tục
  const [rowsPage, setRowsPage] = useState(1);
  const [rowsTotalPages, setRowsTotalPages] = useState(1);
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState(null); // {gcnId, cutIndex, title}
  const [pvPage, setPvPage] = useState(1);
  const [job, setJob] = useState(null); // {job_id, status, row_count, error}
  const pollRef = useRef(null);
  const refreshStatsRef = useRef(() => {}); // SSE chỉ làm tươi STATS (nhẹ), KHÔNG động bảng dòng
  const reviewerDaysMounted = useRef(false); // né gọi getStats trùng lúc mount (đã có refresh() lo)

  function openPreview(p) { setPvPage(1); setPreview(p); }

  async function refreshStats() {
    try {
      setStats(await getStats({
        batchId: batchId || undefined,
        reviewerDays: reviewerDays !== "custom" ? (reviewerDays || undefined) : undefined,
        reviewerFrom: reviewerDays === "custom" ? (reviewerFrom || undefined) : undefined,
        reviewerTo: reviewerDays === "custom" ? (reviewerTo || undefined) : undefined,
      }));
    } catch (e) { toastErr(e.message || e); }
  }

  async function refresh(p = rowsPage) {
    setLoading(true);
    try {
      const [d] = await Promise.all([
        listRows({ batchId: batchId || undefined, review: review || undefined,
                   page: p, pageSize: ROWS_PAGE_SIZE }),
        refreshStats(),
      ]);
      setCols(d.columns || []);
      setRows(d.rows || []);
      setRowsTotal(d.total || 0);
      setDocOffset(d.doc_offset || 0);
      setRowsTotalPages(d.total_pages || 1);
    } catch (e) { toastErr(e.message || e); } finally { setLoading(false); }
  }
  useEffect(() => { refreshStatsRef.current = refreshStats; });

  // Đổi khoảng thời gian bảng "Theo người hậu kiểm" → chỉ gọi lại stats (nhẹ),
  // không đụng tới trang/bộ lọc của bảng dòng phẳng bên dưới. Bỏ qua lần đầu vì
  // effect [batchId, review] bên dưới đã gọi refresh() (kèm stats) lúc mount.
  useEffect(() => {
    if (!reviewerDaysMounted.current) { reviewerDaysMounted.current = true; return; }
    refreshStats();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reviewerDays, reviewerFrom, reviewerTo]);

  function onReviewerRangeChange(from, to) { setReviewerFrom(from); setReviewerTo(to); }

  useEffect(() => {
    listBatches().then((d) => setBatches(d.batches || [])).catch(() => {});
  }, []);
  // Đổi bộ lọc → về trang 1 (không dùng state `rowsPage` cũ để tránh closure lệch nhịp).
  useEffect(() => { setRowsPage(1); refresh(1); /* eslint-disable-next-line */ }, [batchId, review]);
  // SSE (đang xử lý) CHỈ làm tươi các thanh trạng thái trên cùng — KHÔNG tự nạp
  // lại bảng dòng: bảng đó gom-bản-trùng bằng $group quét toàn kho, chạy mỗi
  // event thì nặng Mongo. Người dùng bấm "Làm mới" (hoặc đổi lọc/lật trang) khi
  // muốn xem dữ liệu mới.
  useEffect(() => {
    let t = null;
    const un = subscribeEvents(() => { clearTimeout(t); t = setTimeout(() => refreshStatsRef.current(), 800); });
    return () => { un(); clearTimeout(t); };
  }, []);

  function goToRowsPage(p) { setRowsPage(p); refresh(p); }

  async function csv() {
    try {
      await downloadCsv({ batchId: batchId || undefined, review: review || undefined });
      toastOk("Đã tải CSV");
    } catch (e) { toastErr(e.message || e); }
  }

  // Xuất nền: không cap dòng, không chặn request — worker đọc Mongo qua cursor.
  // operator/viewer bắt buộc chọn 1 lô cụ thể (server yêu cầu batch_id nằm
  // trong phạm vi được gán — không còn "xuất toàn bộ" như thời chi nhánh).
  const canStartExportJob = canExportJob && (isAdmin || !!batchId);
  useEffect(() => () => clearInterval(pollRef.current), []);
  async function startExportJob() {
    clearInterval(pollRef.current);
    try {
      const { job_id } = await createExportJob({
        batchId: batchId || undefined, review: review || undefined,
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
    if (col === "Trạng thái") return STATUS_LABEL[r[col]] || r[col] || "—";
    if (col === "Hậu kiểm") return REVIEW_LABEL[r[col]] || r[col] || "—";
    const v = r[col];
    return v == null ? "" : String(v);
  }

  // Phân trang giờ ở TẦNG HỒ SƠ (mới nhất trước): 1 hồ sơ nhiều thửa = nhiều
  // hàng liền nhau. STT đánh theo HỒ SƠ — chỉ hiện ở hàng đầu mỗi hồ sơ, các
  // hàng thửa sau để trống (giống bảng trích xuất). doc_offset nối số qua trang.
  const sttForRow = useMemo(() => {
    const out = [];
    let seq = 0, last;
    for (const r of rows) {
      if (r._gcn_id !== last) { seq += 1; last = r._gcn_id; out.push(docOffset + seq); }
      else out.push(null);
    }
    return out;
  }, [rows, docOffset]);

  const s = stats || {};
  const st = s.by_status || {};

  return (
    <div className="panel export-view">
      <div className="et-toolbar">
        <h2>Tổng quan</h2>
        <div className="et-filters">
          <select value={batchId} onChange={(e) => setBatchId(e.target.value)}>
            <option value="">Tất cả đợt</option>
            {batches.map((b) => <option key={b.batch_id} value={b.batch_id}>{b.name} · {b.file_count} hồ sơ</option>)}
          </select>
          <button className="ghost sm" onClick={() => refresh()}><Icon name="refresh" size={14} /> Làm mới</button>
        </div>
      </div>

      <div className="seg-row">
        <SegBar title="Trạng thái xử lý" segs={STATUS_SEGS} data={st} pendingKeys={["queued", "processing"]} />
        <SegBar title="Hậu kiểm" segs={REVIEW_SEGS} data={s.by_review || {}} />
      </div>

      {canViewReviewerTable && (
        <ReviewerTable rows={s.by_reviewer || []} days={reviewerDays} onDaysChange={setReviewerDays}
          rangeFrom={reviewerFrom} rangeTo={reviewerTo} onRangeChange={onReviewerRangeChange} />
      )}

      <div className="export-sec">
        <div className="export-head">
          <h3>Xuất dữ liệu <span className="muted">(CSV · mỗi thửa một dòng)</span></h3>
          <div className="et-filters">
            <select value={review} onChange={(e) => setReview(e.target.value)}>
              {Object.entries(REVIEW).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <span className="muted ev-count">{fmt(rowsTotal)} hồ sơ</span>
            <div className="ev-export-btns">
              <button className="primary sm" onClick={csv}><Icon name="download" size={14} /> Tải CSV</button>
              {canExportJob && (
                <button className="ghost sm"
                  disabled={!canStartExportJob || (job && job.status !== "done" && job.status !== "error")}
                  onClick={startExportJob}
                  title={canStartExportJob
                    ? "Không giới hạn số dòng — chạy nền, không cap 5000 dòng như Tải CSV"
                    : "Hãy chọn 1 đợt cụ thể để xuất nền"}>
                  <Icon name="upload" size={14} /> Xuất nền
                </button>
              )}
            </div>
          </div>
        </div>

        {job && (
          <div className="export-job-status">
            {job.status === "queued" && <>Đang chờ worker…</>}
            {job.status === "processing" && <>Đang xuất… {job.row_count ? `(${job.row_count} dòng)` : ""}</>}
            {job.status === "error" && <span className="error" title={job.error}>Lỗi xuất: {friendlyError(job.error)}</span>}
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
              <tr><th className="et-stt">STT</th>{cols.map((c) => <th key={c}>{c}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td className="et-stt">{sttForRow[i] == null ? "" : sttForRow[i]}</td>
                  {cols.map((c) => (
                    <td key={c} title={FILE_COLS.has(c) ? "" : (r[c] == null ? "" : String(r[c]))}>{cell(c, r)}</td>
                  ))}
                </tr>
              ))}
              {!rows.length && (
                <tr><td colSpan={(cols.length || 1) + 1} className="muted center">
                  {loading ? "Đang tải…" : "Chưa có dòng nào khớp bộ lọc."}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>

        <Pager page={rowsPage} totalPages={rowsTotalPages} total={rowsTotal} unit="hồ sơ" onChange={goToRowsPage} />
      </div>

      {preview && (
        <div className="modal-overlay">
          <div className="modal-card">
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
