# AI-HUB — Console số hóa & đối soát Giấy Chứng Nhận (GCN)

Xử lý **theo lô, quy mô lớn** Giấy Chứng Nhận quyền sử dụng đất: thả/nhập lô PDF scan → tự phát
hiện GCN, bóc tách bằng **VLM** → **bảng trích xuất** → **đối soát PDF ↔ dữ liệu bóc ra** →
**hậu kiểm + đặt lại tên + tải bộ**. Tờ bổ sung tự gom về GCN gốc theo **Số phát hành**.

Đang chạy production ở quy mô **hàng triệu hồ sơ/lô** (khách tham chiếu: VP Đăng ký đất đai TP Hà Nội).

Engine detect+extract là VLM tự host (`gemma-4-26B-A4B-NVFP4` qua vLLM/litellm), vendor nguyên
trạng từ `auto-detect-extract-gcn-vlm` (`src/extentions/multimodal/*`).

## Kiến trúc

- **backend/** FastAPI (motor + aioboto3) + **worker async streaming-pool**.
  - `app/` = API + worker + routes + scripts; `src/` = engine GCN vendor (`from src.extentions…`).
  - **Mongo làm hàng đợi** (không RQ): worker poll doc `status=queued`, claim nguyên tử, xử lý,
    reclaim job treo, dead-letter poison.
  - Worker chạy **nhiều file in-flight** + **pool nhiều máy vLLM** (cân tải ít-việc-nhất, failover,
    circuit-breaker). Detect = phân loại biên từng trang song song; giới hạn `AIHUB_MAX_PAGES` (250).
- **frontend/** React/Vite (nginx) — bảng trích xuất, đối soát 2 cột PDF↔GCN, hậu kiểm, admin.
- **compose**: `api · worker · mongo · redis · frontend`. MinIO (kho nguồn + đích) và model VLM là
  **dịch vụ ngoài**.

## Luồng dữ liệu (tóm tắt)

1. `POST /v1/batches` (upload) hoặc `import_jobs` (nhập thư mục kho nguồn) → tạo `gcn{queued}`.
2. Worker `process_doc`: tải PDF (MinIO) → render+xoay → detect + extract song song → normalize →
   gom theo Số phát hành → ghi `extractions/summary/cuts` → SSE tiến độ.
3. `GET /v1/gcn` bảng trích xuất · `PUT /v1/gcn/{id}` hậu kiểm · `/download` tải zip · export CSV.

Chi tiết thuật toán từng bước: **[docs/algorithm.md](docs/algorithm.md)**.

## Cổng (né Parsany)

| dịch vụ | host → container |
|---|---|
| API | 18002 → 8000 |
| Web | 3000 → 80 |
| Mongo | 27018 → 27017 |
| Redis | 56381 → 6379 |

## Chạy (trên máy serve)

```bash
cp .env.example .env               # trỏ MinIO nguồn/đích + VLLM_ENDPOINTS; chỉnh nếu cần
docker compose up -d --build       # bucket + index Mongo tạo tự động lúc API khởi động
# UI: http://<host>:3000
```

Deploy code mới: `git pull && docker compose up -d --build api worker frontend` (chỉ 3 service
build từ code). Sau khi restart/build worker, có thể có job kẹt `processing` → dùng nút **Giải
phóng job kẹt** (Admin → Lỗi) hoặc `POST /v1/gcn/release-stuck`.

## Vận hành nhanh

- **Retry lỗi**: Admin → tab Lỗi (mọi lô + lọc thời gian) hoặc `app/scripts/retry_errors_all.py`.
- **Giải phóng job kẹt**: Admin → tab Lỗi → "Giải phóng job kẹt".
- **Đo hiệu năng**: `docker compose exec worker python -m app.scripts.bench_pipeline tmp/*.pdf --duration 60`.
- **Thử loại giấy mới** (đơn đăng ký · giấy xác nhận đăng ký · phiếu thu thập thông tin):
  `docker compose exec api python -m app.scripts.smoke_e2e_doc_types --api http://localhost:8000 -u admin -p '***' --loai ddk <thư-mục-pdf> --so-luong 5`
  — đẩy PDF qua API thật rồi in bảng KILL + độ điền từng trường. Xem `docs/algorithm.md#7b`.

## Tài liệu (`docs/`)

| File | Nội dung |
|---|---|
| [overall_roadmap.md](docs/overall_roadmap.md) | Tổng quan dự án + roadmap chi tiết (thay project-insight) |
| [algorithm.md](docs/algorithm.md) | Thuật toán các luồng xử lý |
| [features_issues.md](docs/features_issues.md) | Sổ tính năng + issue (gồm phân tích hiệu năng MinIO) |
| [test_eval.md](docs/test_eval.md) | Smoke test + benchmark + eval (máy serve vs cá nhân) |
| [need_exchange.md](docs/need_exchange.md) | Câu hỏi cần làm rõ với khách hàng |

Quyết định thiết kế lịch sử: `PLAN.md`, `PLAN_.md`, `PLAN_CHU_CUOI_MDSDD.md`.

## Bất biến (đừng phá)

1. Kho nguồn **read-only** — không ghi/xóa trên MinIO nguồn của khách.
2. Giữ raw `extractions` — hậu kiểm ghi vào `review.*`.
3. Không `count_documents` ở hot path — tiến độ từ `batch.counts`.
4. Không nạp toàn bộ vào RAM — mọi liệt kê/xóa S3, import đều STREAM theo trang.
5. Khóa nghiệp vụ = **Số phát hành**.
6. Không sửa vendor (`minio_helper`, `multimodal/*`) — bọc ở lớp app.
