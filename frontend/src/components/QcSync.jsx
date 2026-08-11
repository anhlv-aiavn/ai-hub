import React, { useEffect, useState } from "react";
import Icon from "./Icon.jsx";
import {
  getS3Connections,
  getQcSyncConfigs, createQcSyncConfig, updateQcSyncConfig, deleteQcSyncConfig,
  runQcSyncNow, getQcSyncActiveJob, cancelQcSyncJob, getQcSyncStats, getQcSyncItems,
} from "../api.js";
import { toastOk, toastErr } from "../toast.js";

// Trang "QC Sync" (admin) — pipeline MỚI, song song với pipeline GCN chính:
// đồng bộ 1 kho MinIO nguồn đã cấu hình, chấm chất lượng qua qc-scanner-server
// ngoài, nếu đạt thì chạy OCR + cắt trang GCN lưu vào MinIO đích riêng (tab
// "S3 đích (QC Sync)" trong Cấu hình hệ thống). Xử lý thật chạy trong worker
// (app/worker/qc_pipeline.py) — trang này chỉ cấu hình + theo dõi.
const EMPTY_FORM = {
  name: "", source_connection_id: "", prefix: "", dest_connection_id: "",
  interval_seconds: 300, enabled: true,
};

const VERDICT_LABEL = { pass: "Đạt", warn: "Đạt (cảnh báo)", fail: "Không đạt" };
const VERDICT_CLASS = { pass: "dot-ok", warn: "dot-unknown", fail: "dot-err" };
const ITEM_STATUS_OPTIONS = [
  ["", "— Mọi trạng thái —"], ["error", "Lỗi"], ["no_file", "Không thấy file"],
  ["queued", "Đang chờ"], ["processing", "Đang xử lý"], ["done", "Xong"], ["no_gcn", "Không thấy GCN"],
];

function fmtDate(d) {
  if (!d) return "—";
  try { return new Date(d).toLocaleString("vi-VN"); } catch { return String(d); }
}

export default function QcSync() {
  const [configs, setConfigs] = useState([]);
  const [sources, setSources] = useState([]);
  const [dests, setDests] = useState([]);
  const [editing, setEditing] = useState(null); // null=hidden, {}=new, {...}=edit
  const [form, setForm] = useState(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [selectedConfigId, setSelectedConfigId] = useState("");
  const [refreshTick, setRefreshTick] = useState(0); // bump để ActiveJobRun poll lại ngay (không chờ chu kỳ 3s)

  async function refreshConfigs() {
    try { const d = await getQcSyncConfigs(); setConfigs(d.configs || []); }
    catch (e) { toastErr(e.message || e); }
  }
  useEffect(() => {
    refreshConfigs();
    getS3Connections("source").then((d) => setSources(d.connections || [])).catch(() => {});
    getS3Connections("destination", "qc").then((d) => setDests(d.connections || [])).catch(() => {});
  }, []);

  function openNew() { setForm(EMPTY_FORM); setEditing({}); }
  function openEdit(c) {
    setForm({
      name: c.name, source_connection_id: c.source_connection_id, prefix: c.prefix || "",
      dest_connection_id: c.dest_connection_id, interval_seconds: c.interval_seconds, enabled: c.enabled,
    });
    setEditing(c);
  }

  async function save() {
    if (!form.name || !form.source_connection_id || !form.dest_connection_id) {
      toastErr("Điền đủ tên/nguồn/đích"); return;
    }
    setBusy(true);
    try {
      if (editing?.id) await updateQcSyncConfig(editing.id, form);
      else await createQcSyncConfig(form);
      toastOk("Đã lưu kênh đồng bộ");
      setEditing(null); refreshConfigs();
    } catch (e) { toastErr(e.message || e); } finally { setBusy(false); }
  }

  async function remove(c) {
    if (!window.confirm(`Xóa kênh đồng bộ "${c.name}"?`)) return;
    try { await deleteQcSyncConfig(c.id); toastOk("Đã xóa"); refreshConfigs(); }
    catch (e) { toastErr(e.message || e); }
  }

  async function runNow(c) {
    try { await runQcSyncNow(c.id); toastOk("Đã đưa vào hàng chờ — worker sẽ quét ở lượt kế tiếp"); refreshConfigs(); }
    catch (e) { toastErr(e.message || e); }
    finally { setRefreshTick((t) => t + 1); } // dù thành công hay "đã có lượt đang chạy" — hiện badge tiến độ ngay
  }

  return (
    <div className="panel">
      <div className="panel-head">
        <h3><Icon name="scan" size={16} /> QC Sync — kiểm chất lượng + OCR + cắt GCN</h3>
        <button className="primary sm" onClick={openNew}><Icon name="plus" size={13} /> Thêm kênh đồng bộ</button>
      </div>
      <p className="muted small" style={{ padding: "0 16px" }}>
        Đồng bộ liên tục 1 thư mục trên kho MinIO nguồn: mỗi file mới được chấm chất lượng qua
        qc-scanner-server, nếu đạt thì chạy OCR và cắt trang Giấy chứng nhận lưu vào kho MinIO đích
        riêng (cấu hình ở Cấu hình hệ thống → "S3 đích (QC Sync)"). File đã quét sẽ KHÔNG bị quét lại.
      </p>

      {!dests.length && (
        <div className="admin-warn" style={{ margin: "0 16px 12px" }}>
          <Icon name="alertTriangle" size={16} />
          <div>
            <b>Chưa có S3 đích cho QC Sync</b>
            <p>Vào Cấu hình hệ thống → "S3 đích (QC Sync)" để thêm ít nhất 1 cấu hình đích trước khi tạo kênh.</p>
          </div>
        </div>
      )}

      <div className="tbl-dense qc-cfg-tbl" style={{ margin: "0 16px" }}>
        <div className="file-row qc-cfg-row qc-cfg-head">
          <span>Tên</span><span>Nguồn / prefix</span><span>Đích</span><span>Chu kỳ</span>
          <span>Lần quét cuối</span><span />
        </div>
        {configs.map((c) => {
          const src = sources.find((s) => s.id === c.source_connection_id);
          const dest = dests.find((d) => d.id === c.dest_connection_id);
          return (
            <React.Fragment key={c.id}>
              <div className="file-row qc-cfg-row">
                <span className="fr-name">
                  {c.name} {!c.enabled && <span className="muted small">(tắt)</span>}
                </span>
                <span className="fr-meta">{src?.name || c.source_connection_id} · /{c.prefix || ""}</span>
                <span className="fr-meta">{dest?.name || c.dest_connection_id}</span>
                <span className="fr-meta">{c.interval_seconds}s</span>
                <span className="fr-meta">
                  {fmtDate(c.last_run_at)}
                  {c.last_run_status && ` · ${c.last_run_status === "done" ? "OK" : c.last_run_status}`}
                </span>
                <span className="s3-actions">
                  <button className="ghost xs" onClick={() => runNow(c)}>Chạy ngay</button>
                  <button className="ghost xs" onClick={() => setSelectedConfigId(c.id)}>Thống kê</button>
                  <button className="ghost xs" onClick={() => openEdit(c)}>Sửa</button>
                  <button className="ghost xs danger" onClick={() => remove(c)}>Xóa</button>
                </span>
              </div>
              <ActiveJobRun configId={c.id} refreshTick={refreshTick} onDone={refreshConfigs} />
            </React.Fragment>
          );
        })}
        {!configs.length && <div className="muted center" style={{ padding: 16 }}>Chưa có kênh đồng bộ nào.</div>}
      </div>

      {editing && (
        <div className="s3-form" style={{ margin: "12px 16px" }}>
          <h4>{editing.id ? `Sửa: ${editing.name}` : "Thêm kênh đồng bộ QC Sync"}</h4>
          <div className="row">
            <input className="text-input" placeholder="Tên kênh" value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <select className="text-input" value={form.source_connection_id}
              onChange={(e) => setForm({ ...form, source_connection_id: e.target.value })}>
              <option value="">— Chọn S3 nguồn —</option>
              {sources.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </div>
          <div className="row">
            <input className="text-input" placeholder="Prefix / thư mục (để trống = cả bucket)"
              value={form.prefix} onChange={(e) => setForm({ ...form, prefix: e.target.value })} />
            <select className="text-input" value={form.dest_connection_id}
              onChange={(e) => setForm({ ...form, dest_connection_id: e.target.value })}>
              <option value="">— Chọn S3 đích (QC Sync) —</option>
              {dests.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </div>
          <div className="row">
            <label className="field-label" style={{ display: "flex", alignItems: "center", gap: 6 }}>
              Chu kỳ quét lại (giây)
              <input type="number" className="text-input" style={{ width: 100 }} min={30}
                value={form.interval_seconds}
                onChange={(e) => setForm({ ...form, interval_seconds: Number(e.target.value) || 300 })} />
            </label>
            <label className="row" style={{ alignItems: "center" }}>
              <input type="checkbox" checked={form.enabled}
                onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />
              Bật đồng bộ
            </label>
          </div>
          <div className="admin-tab-foot">
            <button className="primary sm" disabled={busy} onClick={save}>Lưu</button>
            <button className="ghost sm" onClick={() => setEditing(null)}>Hủy</button>
          </div>
        </div>
      )}

      {selectedConfigId && (
        <QcSyncDetail configId={selectedConfigId}
          configName={configs.find((c) => c.id === selectedConfigId)?.name}
          onClose={() => setSelectedConfigId("")} />
      )}
    </div>
  );
}

// ── Băng tiến độ lượt quét đang chạy (nếu có) + nút Dừng ────────────────────
// Poll độc lập theo TỪNG kênh (không đợi refreshConfigs của cha) — kênh không
// có lượt nào đang chạy thì render null, không tốn ô trống trên bảng.
function ActiveJobRun({ configId, refreshTick, onDone }) {
  const [job, setJob] = useState(null);
  const [busy, setBusy] = useState(false);

  async function poll() {
    try {
      const d = await getQcSyncActiveJob(configId);
      setJob(d.job);
      if (!d.job) onDone?.();
    } catch { /* lỗi poll thoáng qua — bỏ qua, thử lại chu kỳ sau */ }
  }
  useEffect(() => { poll(); }, [configId, refreshTick]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!job) return;
    const id = setInterval(poll, 3000);
    return () => clearInterval(id);
  }, [job?.id, job?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  async function stop() {
    if (!job) return;
    setBusy(true);
    try { await cancelQcSyncJob(job.id); toastOk("Đã gửi yêu cầu dừng"); await poll(); }
    catch (e) { toastErr(e.message || e); } finally { setBusy(false); }
  }

  if (!job) return null;
  return (
    <div className="qc-active-job">
      <span className="dot dot-unknown" />
      <span>
        {job.status === "queued" ? "Đang chờ worker nhặt…" : "Đang quét…"}{" "}
        <b>{(job.scanned || 0).toLocaleString("vi-VN")}</b> đã liệt kê ·{" "}
        <b>{(job.enqueued || 0).toLocaleString("vi-VN")}</b> file mới
        {!!job.skipped && ` · ${job.skipped.toLocaleString("vi-VN")} đã quét trước đó`}
        {job.cancel_requested && " · đang dừng theo yêu cầu…"}
      </span>
      <button className="ghost xs danger" disabled={busy || job.cancel_requested} onClick={stop}>
        {job.cancel_requested ? "Đang dừng…" : "Dừng"}
      </button>
    </div>
  );
}

// ── Thống kê + bảng item gần nhất của 1 kênh ────────────────────────────────
const RANGES = [["day", "Hôm nay"], ["week", "7 ngày"], ["month", "30 ngày"], ["all", "Tổng"]];
const COUNT_LABELS = [
  ["scanned", "Đã quét"], ["pass", "Đạt"], ["warn", "Đạt (cảnh báo)"], ["fail", "Không đạt"],
  ["ocr_done", "Đã OCR+cắt"], ["no_gcn", "Không thấy GCN"], ["error", "Lỗi"],
];

function QcSyncDetail({ configId, configName, onClose }) {
  const [range, setRange] = useState("week");
  const [stats, setStats] = useState(null);
  const [items, setItems] = useState([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [page, setPage] = useState(1);
  const pageSize = 20;

  useEffect(() => {
    getQcSyncStats({ configId, range }).then((d) => setStats(d.counts || {})).catch(() => setStats({}));
  }, [configId, range]);

  useEffect(() => { setPage(1); }, [statusFilter]);

  useEffect(() => {
    getQcSyncItems({ configId, status: statusFilter || undefined, page, pageSize })
      .then((d) => setItems(d.items || []))
      .catch(() => setItems([]));
  }, [configId, statusFilter, page]);

  return (
    <div className="s3-form" style={{ margin: "0 16px 16px" }}>
      <div className="panel-head" style={{ padding: 0 }}>
        <h4>Thống kê — {configName || configId}</h4>
        <button className="ghost xs" onClick={onClose}><Icon name="x" size={13} /> Đóng</button>
      </div>

      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        {RANGES.map(([k, label]) => (
          <button key={k} className={`ghost xs ${range === k ? "active" : ""}`} onClick={() => setRange(k)}>{label}</button>
        ))}
      </div>
      <div className="row" style={{ flexWrap: "wrap", gap: 12, marginTop: 8 }}>
        {COUNT_LABELS.map(([k, label]) => (
          <div key={k} className="muted small" style={{ minWidth: 110 }}>
            {label}: <b>{(stats?.[k] ?? 0).toLocaleString("vi-VN")}</b>
          </div>
        ))}
      </div>

      <div className="row" style={{ alignItems: "center", gap: 8, marginTop: 16 }}>
        <h4 style={{ margin: 0 }}>File gần đây {statusFilter && "— lọc theo trạng thái"}</h4>
        <select className="text-input" style={{ width: "auto" }} value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}>
          {ITEM_STATUS_OPTIONS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>
      </div>
      <div className="tbl-dense s3-tbl">
        <div className="file-row s3-head">
          <span>S3 key</span><span>Trạng thái</span><span>Verdict QC</span><span>Lý do / lỗi</span><span>Lúc</span>
        </div>
        {items.map((it) => {
          const isError = it.status === "error" || it.status === "no_file";
          return (
            <div className="file-row s3-row" key={it.id}>
              <span className="fr-name" title={it.s3_key}>{it.s3_key}</span>
              <span className="fr-meta">
                {isError && <span className="dot dot-err" />}{" "}{it.status}
                {it.attempts > 1 && ` (${it.attempts} lần)`}
              </span>
              <span className="fr-meta">
                {it.qc?.verdict && (
                  <><span className={`dot ${VERDICT_CLASS[it.qc.verdict] || "dot-unknown"}`} />{" "}
                  {VERDICT_LABEL[it.qc.verdict] || it.qc.verdict}</>
                )}
              </span>
              <span className="fr-meta" title={isError ? it.error : undefined}
                style={isError ? { color: "var(--err)", whiteSpace: "normal" } : undefined}>
                {isError
                  ? `${it.error || "Lỗi không rõ"}${it.error_kind ? ` (${it.error_kind})` : ""}`
                  : (it.qc?.reasons || []).map((r) => r.code).join(", ")}
              </span>
              <span className="fr-meta">{fmtDate(it.finished_at || it.created_at)}</span>
            </div>
          );
        })}
        {!items.length && <div className="muted center" style={{ padding: 16 }}>Chưa có file nào.</div>}
      </div>
      <div className="row" style={{ gap: 8, marginTop: 8 }}>
        <button className="ghost xs" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>← Trang trước</button>
        <span className="muted small">Trang {page}</span>
        <button className="ghost xs" disabled={items.length < pageSize} onClick={() => setPage((p) => p + 1)}>Trang sau →</button>
      </div>
    </div>
  );
}
