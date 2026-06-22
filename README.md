# AI-HUB — console số hóa & đối soát Giấy Chứng Nhận (GCN)

Sản phẩm **riêng** (tách Parsany) để **xử lý theo lô + đối soát** Giấy Chứng Nhận quyền sử dụng đất:
thả lô PDF → tự phát hiện GCN, bóc tách (VLM) → **bảng trích xuất** → **đối soát PDF ↔ GCN bóc ra**
→ **hậu kiểm + đặt lại tên + tải bộ**. Tờ bổ sung tự gom về GCN gốc theo **Số phát hành**.

Engine detect+extract GCN vendor nguyên trạng từ `auto-detect-extract-gcn-vlm`
(`src/extentions/multimodal/pipeline.detect_and_extract`). MongoDB riêng (chạy trong compose);
MinIO dùng kho **có sẵn** `storage.ai-hub.tumiki.org`; model VLM dùng endpoint **public**
`gemma-4-26B-A4B-NVFP4` (`14.232.240.254:30000`).

## Kiến trúc
- **backend/** FastAPI (motor + aioboto3) + worker async (streaming pool, poll Mongo).
  - `app/` API + worker; `src/` = engine GCN vendor (giữ import `from src.extentions…`).
  - Worker chạy NHIỀU file in-flight + 1 semaphore VLM toàn cục (`MAX_VLM_CONCURRENT`)
    → tận dụng vLLM dynamic-batch. File lớn: detect chia cửa sổ song song; giới hạn
    `AIHUB_MAX_PAGES` (mặc định 250).
- **frontend/** React/Vite (nginx) — tái dùng component Parsany (Icon, Dropzone, EditableTree,
  PdfViewer→GcnPdf, style.css teal/ivory).
- **compose**: api · worker · mongo · minio · redis · frontend.

## Cổng (né Parsany)
| dịch vụ | host |
|---|---|
| API | 18002 → 8000 |
| Web | 3000 → 80 |
| Mongo | 27018 |
| Redis | 56381 |

MinIO (`storage.ai-hub.tumiki.org`) và model VLM (`14.232.240.254:30000`) là dịch vụ ngoài —
không chạy trong compose.

## Chạy
```bash
cp .env.example .env          # mặc định đã trỏ MinIO + model thật; chỉnh nếu cần
docker compose up -d --build  # bucket `ai-hub` + index Mongo tạo tự động lúc API khởi động
# UI: http://<host>:3000
```

## Luồng dữ liệu
1. `POST /v1/batches` (nhiều PDF) → MinIO + `gcn`(queued). Worker tự poll & claim.
2. worker `process_doc`: tải PDF → render+xoay → detect(windowed) + extract(song song) → normalize → ghi
   `extractions / group_key / summary` → publish SSE.
3. `GET /v1/gcn` bảng trích xuất (gom theo Số phát hành); `GET /v1/gcn/{id}` chi tiết;
   `/page/{n}` ảnh trang; `PUT /v1/gcn/{id}` hậu kiểm; `/download` tải zip (PDF + JSON).

## Mongo
- `batch{_id,name,created_at,file_count,status}`
- `gcn{_id,batch_id,filename,s3_key,status,page_count,extractions[],
  extracted_so_phat_hanhs[],group_key,summary,review{display_name,overrides,status,reviewer,at}}`
  — raw `extractions` giữ nguyên; hậu kiểm ghi `review.*`.

## Pha sau
Tiến độ %/retry từng bước, cây lịch sử giấy (chuỗi Biến động/Số phát hành), dashboard.
Xem `PLAN.md`.
