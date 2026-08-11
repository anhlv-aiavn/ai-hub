import React, { useEffect, useRef, useState } from "react";
import Icon from "./Icon.jsx";
import {
  getS3Connections,
  getQcSyncConfigs, createQcSyncConfig, updateQcSyncConfig, deleteQcSyncConfig,
  runQcSyncNow, getQcSyncActiveJob, cancelQcSyncJob, getQcSyncStats, getQcSyncItems,
  retryQcSyncItem, deleteQcSyncItem, clearQcSyncConfigItems, qcSyncSourcePdfUrl, qcSyncCutPdfUrl,
  getQcWards, syncQcWards,
} from "../api.js";
import { toastOk, toastErr } from "../toast.js";

// Trang "QC Sync" (admin) — pipeline MỚI, song song với pipeline GCN chính:
// đồng bộ 1 kho MinIO nguồn đã cấu hình, chấm chất lượng qua qc-scanner-server
// ngoài, nếu đạt thì chạy OCR + cắt trang GCN lưu vào MinIO đích riêng (tab
// "S3 đích (QC Sync)" trong Cấu hình hệ thống). Xử lý thật chạy trong worker
// (app/worker/qc_pipeline.py) — trang này chỉ cấu hình + theo dõi.
const EMPTY_FORM = {
  name: "", ward_name: "", source_connection_id: "", prefix: "", dest_connection_id: "",
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

// Menu "⋯" gọn cho các thao tác PHỤ của 1 dòng — cùng pattern popover với
// AccountMenu.jsx (click-outside đóng, Escape đóng). Dùng để bớt rối khi 1
// dòng có nhiều thao tác (trước đây 6 nút phẳng trên 1 dòng gây "ríu rít").
function RowMenu({ items }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    function onDoc(e) { if (!ref.current?.contains(e.target)) setOpen(false); }
    function onKey(e) { if (e.key === "Escape") setOpen(false); }
    document.addEventListener("mousedown", onDoc, true);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDoc, true);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open]);

  return (
    <div className="row-menu" ref={ref}>
      <button type="button" className="ghost sm row-menu-trigger" aria-haspopup="menu" aria-expanded={open}
        title="Thêm thao tác" onClick={() => setOpen((v) => !v)}>⋯</button>
      {open && (
        <div className="row-menu-pop" role="menu">
          {items.map((it, i) => (
            <button key={i} type="button" className={`row-menu-item ${it.danger ? "is-danger" : ""}`}
              role="menuitem" onClick={() => { setOpen(false); it.onClick(); }}>{it.label}</button>
          ))}
        </div>
      )}
    </div>
  );
}

// Đồng bộ danh sách Phường/Xã (dùng để tự hiện tên P/X thay mã kênh, khớp
// theo "Tên kênh" == mã xã) — URL API do admin tự nhập mỗi lần đồng bộ,
// backend gọi HTTP GET an toàn bằng httpx (KHÔNG chạy lệnh curl qua shell).
function WardSyncPanel() {
  const [wards, setWards] = useState([]);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);

  async function refresh() {
    try { const d = await getQcWards(); setWards(d.wards || []); }
    catch (e) { toastErr(e.message || e); }
  }
  useEffect(() => { refresh(); }, []);

  async function sync() {
    if (!url.trim()) { toastErr("Nhập URL API danh sách xã"); return; }
    if (wards.length && !window.confirm(
      `Đã có ${wards.length} xã trong hệ thống.\n\n` +
      `Đồng bộ sẽ GHI ĐÈ TOÀN BỘ danh sách cũ bằng dữ liệu mới lấy từ URL này.\n\nTiếp tục?`
    )) return;
    setBusy(true);
    try {
      const res = await syncQcWards(url.trim());
      toastOk(`Đã đồng bộ ${res.imported} xã${res.skipped ? ` (bỏ qua ${res.skipped} dòng lỗi)` : ""}`);
      refresh();
    } catch (e) { toastErr(e.message || e); } finally { setBusy(false); }
  }

  return (
    <div className="qc-wardsync" style={{ margin: "0 16px 12px" }}>
      <button type="button" className="qc-wardsync-toggle" onClick={() => setOpen((v) => !v)}>
        <Icon name={open ? "chevronDown" : "chevronRight"} size={14} />
        Danh sách Phường/Xã <b>({wards.length})</b>
      </button>
      {open && (
        <div className="qc-wardsync-body">
          <p className="muted small">
            Dùng để tự hiện tên Phường/Xã thay mã kênh ở bảng dưới và trang Tổng quan (khớp theo
            "Tên kênh" = mã xã). Đồng bộ sẽ ghi đè toàn bộ danh sách hiện có.
          </p>
          <div className="row">
            <input className="text-input" placeholder='URL API trả về {"data":[{"maXa","tenXa"}],"success"}'
              value={url} onChange={(e) => setUrl(e.target.value)} />
            <button className="primary sm" disabled={busy} onClick={sync}>
              {busy ? "Đang đồng bộ…" : "Đồng bộ"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
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
      name: c.name, ward_name: c.ward_name || "", source_connection_id: c.source_connection_id,
      prefix: c.prefix || "", dest_connection_id: c.dest_connection_id,
      interval_seconds: c.interval_seconds, enabled: c.enabled,
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
    if (!window.confirm(
      `XÓA HẲN kênh đồng bộ "${c.name}"?\n\n` +
      `Xóa cả lịch sử quét, lịch sử job và số liệu thống kê của kênh này (không khôi phục được).\n` +
      `KHÔNG xóa file đã cắt đã lưu ở MinIO đích. Muốn quét lại sau này phải tạo kênh mới.\n\n` +
      `Tiếp tục?`
    )) return;
    try {
      const res = await deleteQcSyncConfig(c.id);
      toastOk(`Đã xóa kênh (${res.deleted_items ?? 0} file, ${res.deleted_jobs ?? 0} job trong lịch sử)`);
      refreshConfigs();
    } catch (e) { toastErr(e.message || e); }
  }

  async function runNow(c) {
    try { await runQcSyncNow(c.id); toastOk("Đã đưa vào hàng chờ — worker sẽ quét ở lượt kế tiếp"); refreshConfigs(); }
    catch (e) { toastErr(e.message || e); }
    finally { setRefreshTick((t) => t + 1); } // dù thành công hay "đã có lượt đang chạy" — hiện badge tiến độ ngay
  }

  async function togglePause(c) {
    try {
      await updateQcSyncConfig(c.id, { items_paused: !c.items_paused });
      toastOk(c.items_paused ? "Đã tiếp tục xử lý — worker nhặt lại file đang chờ" : "Đã tạm dừng xử lý file đang chờ");
      refreshConfigs();
    } catch (e) { toastErr(e.message || e); }
  }

  async function clearItems(c) {
    if (!window.confirm(
      `XÓA TOÀN BỘ lịch sử quét của kênh "${c.name}" (cả thư mục)?\n\n` +
      `Mọi file sẽ được coi là "mới" và quét + QC + OCR lại từ đầu ở lượt kế tiếp.\n` +
      `KHÔNG xóa file đã cắt đã lưu ở MinIO đích, KHÔNG xóa lịch sử các lượt quét.\n\n` +
      `Không thể hoàn tác. Tiếp tục?`
    )) return;
    try {
      const res = await clearQcSyncConfigItems(c.id);
      toastOk(`Đã xóa ${res.deleted_items} file khỏi lịch sử quét`);
      refreshConfigs();
    } catch (e) { toastErr(e.message || e); }
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

      <WardSyncPanel />

      {!dests.length && (
        <div className="admin-warn" style={{ margin: "0 16px 12px" }}>
          <Icon name="alertTriangle" size={16} />
          <div>
            <b>Chưa có S3 đích cho QC Sync</b>
            <p>Vào Cấu hình hệ thống → "S3 đích (QC Sync)" để thêm ít nhất 1 cấu hình đích trước khi tạo kênh.</p>
          </div>
        </div>
      )}

      <div className="qc-cfg-list" style={{ margin: "0 16px" }}>
        {configs.map((c) => {
          const src = sources.find((s) => s.id === c.source_connection_id);
          const dest = dests.find((d) => d.id === c.dest_connection_id);
          return (
            <div className="qc-cfg-card" key={c.id}>
              <div className="qc-cfg-card-top">
                <div className="qc-cfg-card-title">
                  <span className="qc-cfg-card-name" title={c.ward_name || c.name}>{c.ward_name || c.name}</span>
                  {c.ward_name && c.ward_name !== c.name && <span className="qc-cfg-card-code">Mã: {c.name}</span>}
                  {!c.enabled && <span className="badge off">Tắt</span>}
                  {c.items_paused && <span className="badge paused">Tạm dừng xử lý</span>}
                </div>
                <div className="qc-cfg-card-actions">
                  <button className="ghost sm" onClick={() => runNow(c)}>Chạy ngay</button>
                  <button className="ghost sm" onClick={() => openEdit(c)}>Sửa</button>
                  <RowMenu items={[
                    {
                      label: c.items_paused ? "Tiếp tục xử lý" : "Tạm dừng xử lý",
                      onClick: () => togglePause(c),
                    },
                    { label: "Xem thống kê", onClick: () => setSelectedConfigId(c.id) },
                    { label: "Xóa dữ liệu đã quét", onClick: () => clearItems(c), danger: true },
                    { label: "Xóa kênh", onClick: () => remove(c), danger: true },
                  ]} />
                </div>
              </div>
              <div className="qc-cfg-card-meta">
                <span title="Nguồn / prefix">
                  <Icon name="folder" size={12} /> {src?.name || c.source_connection_id}
                  {c.prefix ? ` · /${c.prefix}` : ""}
                </span>
                <span>→ {dest?.name || c.dest_connection_id}</span>
                <span>Chu kỳ {c.interval_seconds}s</span>
                <span>
                  Quét lần cuối: {fmtDate(c.last_run_at)}
                  {c.last_run_status && ` (${c.last_run_status === "done" ? "OK" : c.last_run_status})`}
                </span>
              </div>
              <ActiveJobRun configId={c.id} refreshTick={refreshTick} onDone={refreshConfigs} />
            </div>
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
            <input className="text-input" placeholder="Tên Phường/Xã (để trống = tự lấy từ danh sách đã đồng bộ)"
              value={form.ward_name} onChange={(e) => setForm({ ...form, ward_name: e.target.value })} />
          </div>
          <div className="row">
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

  async function loadItems() {
    try { const d = await getQcSyncItems({ configId, status: statusFilter || undefined, page, pageSize }); setItems(d.items || []); }
    catch { setItems([]); }
  }
  useEffect(() => { loadItems(); }, [configId, statusFilter, page]); // eslint-disable-line react-hooks/exhaustive-deps

  async function retryItem(it) {
    if (!window.confirm(
      `Chạy lại QC + OCR cho file này?\n${it.s3_key}\n\n` +
      `Kết quả QC/OCR cũ sẽ bị xóa, worker xử lý lại từ đầu.`
    )) return;
    try { await retryQcSyncItem(it.id); toastOk("Đã đưa lại vào hàng chờ"); loadItems(); }
    catch (e) { toastErr(e.message || e); }
  }

  async function removeItem(it) {
    if (!window.confirm(
      `Xóa lịch sử quét của file này?\n${it.s3_key}\n\n` +
      `File sẽ được coi là "mới" và quét lại ở lượt sau. KHÔNG xóa file đã cắt (nếu có) đã lưu ở MinIO đích.`
    )) return;
    try { await deleteQcSyncItem(it.id); toastOk("Đã xóa"); loadItems(); }
    catch (e) { toastErr(e.message || e); }
  }

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
      <div className="tbl-dense qc-items-tbl">
        <div className="file-row qc-item-row qc-item-head">
          <span>S3 key (bấm để xem PDF nguồn)</span><span>Trạng thái</span><span>Verdict QC</span>
          <span>Lý do / lỗi</span><span>File đã cắt</span><span>Lúc</span><span>Thao tác</span>
        </div>
        {items.map((it) => {
          const isError = it.status === "error" || it.status === "no_file";
          const canRetry = it.status !== "processing" && it.status !== "queued";
          return (
            <div className="file-row qc-item-row" key={it.id}>
              <a className="fr-name" href={qcSyncSourcePdfUrl(it.id)} target="_blank" rel="noopener noreferrer"
                title={`Xem PDF nguồn: ${it.s3_key}`}>{it.s3_key}</a>
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
              <span className="fr-meta qc-cuts-cell">
                {(it.ocr?.cuts || []).length
                  ? it.ocr.cuts.map((cut) => (
                      <a key={cut.index} href={qcSyncCutPdfUrl(it.id, cut.index)} target="_blank"
                        rel="noopener noreferrer" title={`Xem file đã cắt: ${cut.name}`}>{cut.name}</a>
                    ))
                  : "—"}
              </span>
              <span className="fr-meta">{fmtDate(it.finished_at || it.created_at)}</span>
              <span className="s3-actions">
                <button className="ghost xs" disabled={!canRetry} title={!canRetry ? "Đang chờ/đang xử lý" : undefined}
                  onClick={() => retryItem(it)}>Chạy lại</button>
                <button className="ghost xs danger" onClick={() => removeItem(it)}>Xóa</button>
              </span>
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
