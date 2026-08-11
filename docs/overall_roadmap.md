# AI-HUB — Tổng quan dự án & Roadmap

> Thay cho "project-insight". Đây là **điểm vào** cho người mới: dự án là gì, đang ở đâu,
> đi về đâu. Chi tiết kỹ thuật ở `algorithm.md`; việc cần làm ở `features_issues.md`;
> cách kiểm ở `test_eval.md`; việc cần hỏi khách ở `need_exchange.md`.

---

## 1. Dự án là gì

**AI-HUB** là console **số hóa + đối soát Giấy Chứng Nhận (GCN) quyền sử dụng đất** theo lô,
quy mô lớn. Đầu vào: PDF scan GCN (upload hoặc import từ kho MinIO nguồn có sẵn). Đầu ra: dữ
liệu có cấu trúc (chủ sử dụng, thửa đất, mục đích sử dụng, biến động, chủ cuối…), gom tờ bổ sung
về GCN gốc theo **Số phát hành**, cho phép **hậu kiểm** (đối soát PDF ↔ dữ liệu bóc ra), đặt lại
tên và tải bộ.

Khách hàng tham chiếu trên UI: **VP Đăng ký đất đai TP Hà Nội**. Quy mô thực tế: **hàng triệu**
hồ sơ/lô (ảnh chụp màn hình cho thấy lô ~1.06M và ~320K).

**Engine trích xuất** là VLM (vLLM tự host, model `gemma-4-26B-A4B-NVFP4`) — vendor nguyên trạng
từ `auto-detect-extract-gcn-vlm`, gọi qua litellm.

## 2. Kiến trúc (một đoạn)

FastAPI (API) + **worker async streaming-pool** dùng **Mongo làm hàng đợi**; PDF ở **MinIO**
(kho nguồn read-only + kho đích ghi kết quả/cut); **Redis** cho SSE; **pool nhiều máy vLLM** cân
tải + failover. Frontend React/Vite (nginx). Tất cả trong `docker-compose`: `api · worker ·
mongo · redis · frontend` (MinIO + model VLM là dịch vụ ngoài). Chi tiết luồng: `algorithm.md`.

## 3. Nguyên tắc thiết kế (bất biến)

1. **Kho nguồn read-only** — không bao giờ ghi/xóa trên MinIO nguồn của khách.
2. **Giữ raw `extractions`** — hậu kiểm ghi vào `review.*`, không phá dữ liệu gốc.
3. **Không `count_documents` ở hot path** — tiến độ đọc từ `batch.counts` duy trì tăng dần.
4. **Không nạp toàn bộ vào RAM** — mọi liệt kê/xóa S3, import thư mục đều STREAM theo trang.
5. **Khóa nghiệp vụ = Số phát hành** — gom tờ bổ sung + phát hiện nghi trùng theo khóa này.
6. **Xử lý phải tự phục hồi** — claim nguyên tử, reclaim job treo, dead-letter poison.

## 4. Hiện trạng (2026-07)

- Pipeline ingest→extract→gom→hậu kiểm **đã chạy production** ở quy mô triệu hồ sơ.
- Đã có: pool đa vLLM + circuit-breaker, counter lô, SSE gộp, retry lỗi (mọi lô + lọc thời
  gian), giải phóng job kẹt, dry-run, phân quyền/audit. Xem `features_issues.md §C`.
- **Nút thắt lớn nhất chưa xử lý: hiệu năng tầng MinIO** (tạo connection mới mỗi request) →
  GPU đói việc. Đây là ưu tiên số 1 của roadmap. Xem `features_issues.md#perf-minio-pool`.

## 5. Bắc Nam của bài toán tốc độ

Throughput toàn hệ = **min(năng lực GPU, năng lực render CPU, năng lực I/O MinIO)**. Mục tiêu vận
hành: "1 phút / 15 phút xử lý được bao nhiêu bộ GCN". Công cụ đo đã có: `bench_pipeline` bóc tách
thời gian đi đâu (render vs detect vs extract) và tính **VLM utilization**:
- utilization ~1.0 → GPU là trần → nới `MAX_VLM_CONCURRENT` / thêm GPU / giảm token ảnh (PERF-6,
  chỉ chỉnh DPI/độ phân giải — KHÔNG đổi định dạng PNG, xem features_issues #decide-png).
- utilization thấp + `vlm_wait` thấp → **nghẽn ở nơi khác** (rất có thể MinIO PERF-1 hoặc render).

Giả thuyết làm việc: hiện đang nghẽn I/O MinIO (handshake mỗi call) khiến GPU không no. **Việc
đầu tiên của roadmap là chứng minh/bác bỏ bằng số đo, rồi sửa PERF-1.**

---

## 6. Roadmap chi tiết

### Giai đoạn 0 — Đo & chốt nút thắt (1–2 ngày) 🎯 ĐANG TỚI
- [ ] Chạy `bench_pipeline --duration 900` trên máy serve, ghi CSV baseline (files/phút,
      utilization, breakdown). — `test_eval.md`
- [ ] Xác nhận nút thắt: nếu utilization thấp → I/O; đo riêng thời gian `get_pdf`.
- [ ] Kiểm `explain()` các query claim/fairness để xác nhận PERF-7 (index).

### Giai đoạn 1 — Tối ưu MinIO→trích xuất (trọng tâm)
- [ ] **PERF-1** Client MinIO pool sống lâu (tái dùng connection) — bọc ở `storage.py`, đo lại.
- [ ] **PERF-3** Bỏ `os.fsync`/temp-file trong render.
- [ ] **PERF-4** Gộp đếm-trang vào render; giảm serialize qua ProcessPool.
- [ ] **PERF-7** Thêm compound index `{status,batch_id}`, `{status,started_at}`.
- **Tiêu chí ra**: throughput files/phút tăng đo được; VLM utilization tiến gần 1.0.

### Giai đoạn 2 — Tinh chỉnh & chất lượng
- [ ] **PERF-6** Quét DPI/max-size/thinking trên tập vàng (tốc độ vs chính xác).
- [ ] **PERF-5** Dời `_refresh_dup_group` khỏi hot path (backfill/queue phụ).
- [ ] **OPS-1** Tách NoSuchKey khỏi rổ retry (`missing_source`/`skip`).
- [ ] **N-03** Dashboard hiệu năng (bề mặt hóa bench: files/phút, utilization, breakdown).

### Giai đoạn 3 — Vận hành & nghiệp vụ
- [ ] **N-02** Trang Dead-letter soi poison + hành động.
- [ ] **N-05/OPS-3** Chính sách gộp bản trùng nội dung (cần chốt với khách — `need_exchange.md`).
- [ ] **N-06** Retry-từng-bước (chạy lại chỉ extract, tái dùng ảnh đã render).
- [ ] Hậu kiểm: nâng năng suất chuyên viên (phím tắt, hàng chờ ưu tiên, thống kê người duyệt).

### Giai đoạn 4 — Mở rộng
- [ ] **N-04** Cây lịch sử giấy (chuỗi Biến động/Số phát hành theo thời gian).
- [ ] Báo cáo/nghiệm thu theo yêu cầu khách (chờ làm rõ — `need_exchange.md`).

---

## 7. Rủi ro & phụ thuộc

| Rủi ro | Ảnh hưởng | Giảm thiểu |
|--------|-----------|------------|
| MinIO đích là cổng public quá tải | 502 khi ghi cut | `AIHUB_BUILD_CUTS=false`; PERF-1 pool |
| vLLM restart giữa chừng | lỗi hàng loạt `transient` | circuit-breaker + retry-errors (đã có) |
| Chất lượng scan kém / NoSuchKey | error/no_gcn cao | OPS-1; làm rõ chất lượng nguồn với khách |
| Ảnh hưởng chất lượng khi đổi DPI/độ phân giải | sai dữ liệu bóc ra | eval tập vàng trước khi đổi; GIỮ PNG (không đổi định dạng) |
| Sửa vendor `minio_helper`/`multimodal` | lệch bản gốc | bọc ở lớp app, không sửa vendor |

## 8. Tài liệu liên quan
- `algorithm.md` — thuật toán từng luồng.
- `features_issues.md` — sổ tính năng + issue (có mã PERF-*/OPS-*/F-*/N-*).
- `test_eval.md` — smoke test + cách benchmark/eval.
- `need_exchange.md` — câu hỏi cần làm rõ với khách hàng.
- `PLAN.md`, `PLAN_.md`, `PLAN_CHU_CUOI_MDSDD.md` (gốc) — quyết định thiết kế lịch sử.
