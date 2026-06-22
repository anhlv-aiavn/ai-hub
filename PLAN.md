# AI-HUB — Plan: console số hóa & đối soát Giấy Chứng Nhận (GCN)

> Trạng thái: ĐÃ DUYỆT HƯỚNG, chờ build. Repo riêng tại `/Users/bags/prj/ai-hub`.
> Máy này chỉ lưu code — verify trên máy run (.199). Push GitLab branch `dev`.

## Context

Sản phẩm **riêng** (tách Parsany) để **xử lý lô + đối soát** Giấy Chứng Nhận quyền sử dụng đất.
Engine detect+extract GCN **đã có và CỐ ĐỊNH** ở `/Users/bags/prj/auto-detect-extract-gcn-vlm`
(hàm `src.extentions.multimodal.pipeline.detect_and_extract`), lưu **MongoDB**, PDF ở **MinIO**,
khóa nghiệp vụ = **Số phát hành**. Phần thiếu = **console**: thả lô PDF → chạy trích → **bảng trích
xuất** → **đối soát PDF ↔ GCN bóc ra** → **hậu kiểm + đặt lại tên**.

## Quyết định đã chốt
- Repo mới độc lập `/Users/bags/prj/ai-hub`, brand **AI-HUB**.
- MVP **đầy đủ**: **upload→extract NGAY trong app** (đóng gói/vendor engine GCN), không chỉ view.
- **Kho riêng** cho AI-HUB: Mongo + MinIO của riêng nó; **dùng chung đội model VLM trên .199**.
- Khóa gom tờ bổ sung về GCN gốc = **Số phát hành**.
- MVP tới: **hậu kiểm + đặt tên + tải bộ đã gom**. (Tiến độ %/retry-từng-bước, cây lịch sử giấy,
  dashboard = pha sau; data model để mở rộng được.)

## Kiến trúc (standalone, lift pattern từ Parsany)
`/Users/bags/prj/ai-hub/`:
- **backend/** FastAPI async (**motor** + **aioboto3**) + **RQ worker** (Redis) — mượn cấu trúc Parsany
  (`api/routes`, `worker/queue.py`+`run_job.py`, `config.py`).
- **engine/** = **VENDOR** trọn gói GCN từ `auto-detect-extract-gcn-vlm`:
  `multimodal/{pipeline,detect_gcn,extract_gcn,vlm_client,make,prompt,normalize_dang_ky,models/dat}.py`
  + `extentions/{minio_helper,mongo_helper}.py` + asset onnx orientation.
  Entry backend gọi: `detect_and_extract(pdf_bytesio, force_thinking=False)` → list record GCN.
- **frontend/** React/Vite (nginx) — **tái dùng component Parsany**: Inbox/batch, PdfViewer (ảnh trang
  render server), EditableTree (sửa trường lồng), Dropzone, Icon, TagInput, style.css (teal/ivory).
- **compose**: api · worker · mongo · minio · redis · frontend. Port né Parsany (gợi ý api 18002,
  web 18082, mongo 27018, minio 59002, redis 56381). `extra_hosts host.docker.internal:host-gateway`;
  env `VLLM_BASE_URL` trỏ model .199 (gemma 2901).
- Deps engine: litellm, pypdfium2, opencv-python-headless, onnxruntime-gpu, pillow, numpy, motor,
  aioboto3, json_repair, python-dotenv, fastapi, uvicorn, rq, redis, python-multipart, tqdm.

## Schema GCN (từ engine — để dựng bảng & đối soát)
`detect_and_extract` trả `[{page_indices, result, error, page_count, skip_reason}]`; `result`:
```
{"Đăng ký": [ {
  "Giấy chứng nhận": {"Số phát hành","Mã vạch","Số vào sổ","Ngày cấp"},
  "Chủ sử dụng": [{"Loại đối tượng","Tên chủ","Năm sinh","Giới tính","Loại giấy tờ","Số giấy tờ","Ngày cấp định danh","Nơi cấp","Địa chỉ"}],
  "Thửa đất": [{"Số thứ tự thửa","Số hiệu tờ bản đồ","Diện tích","Địa chỉ","Mục đích sử dụng":[{...}]}],
  "Thông tin nhà ở": [{...}],
  "Biến động": [{...}]
} ] }
```

## Data model (MongoDB — kho riêng AI-HUB)
- **`batch`**: `{_id, name, created_at, file_count, status}`.
- **`gcn`** (1 doc / 1 PDF): `{_id, batch_id, filename, s3_key, status(queued|processing|done|error),
  page_count, extractions:[record], extracted_so_phat_hanhs:[], group_key(Số phát hành chính),
  review:{display_name, overrides:{<path>:value}, status(unreviewed|needs_review|reviewed), reviewer, at},
  created_at, extracted_at, error}`. **Giữ raw `extractions`**; hậu kiểm ghi `review.*` (không phá raw).

## Backend (mượn pattern Parsany)
- `config.py`: MONGO_URI/MONGO_DB, MinIO (ENDPOINT/KEY/BUCKET), VLLM_*, redis_url, ports.
- `storage/blob.py`: put/get PDF + render 1 trang PNG (pdfium) phục vụ đối soát.
- routes:
  - `POST /v1/batches` (multipart nhiều PDF) → MinIO + tạo `gcn`(queued) + enqueue → trả batch.
  - `GET /v1/batches`, `GET /v1/batches/{id}` (rollup).
  - `GET /v1/gcn` — **bảng trích xuất**: lọc batch/status, search Số phát hành; cột tóm tắt
    (filename, status, group_key, Số phát hành, Số vào sổ, chủ, thửa/tờ bản đồ, page_count, #GCN).
  - `GET /v1/gcn/{id}` — chi tiết extractions + review.
  - `GET /v1/gcn/{id}/page/{n}` — ảnh trang PDF (đối soát).
  - `PUT /v1/gcn/{id}` — hậu kiểm: review.display_name/overrides/status.
  - `GET /v1/gcn/{id}/download` — tải bộ (PDF + JSON).
- `worker/run_job.py`: load gcn → PDF MinIO → `detect_and_extract` → set extractions/page_count/
  group_key/extracted_so_phat_hanhs/status → Mongo; publish SSE event.

## Frontend (tái dùng Parsany UI)
- **Tạo việc**: Dropzone nhiều PDF → tạo lô → sang Hộp giấy.
- **Bảng trích xuất**: list/bảng GCN, **gom/sắp theo Số phát hành**; cột tóm tắt + badge trạng thái +
  gcn_match; lọc + tìm; live SSE.
- **Đối soát** (bấm dòng → full-width): **trái PDF** (ảnh trang `/page/{n}`) | **phải GCN bóc ra**
  (Đăng ký→các khối, EditableTree sửa được) cạnh nhau; **đặt lại tên** + Duyệt + Tải. Tái dùng
  PdfViewer + EditableTree.

## Tái dùng
- Engine GCN: `detect_and_extract` + helpers (vendor nguyên trạng).
- Parsany FE: PdfViewer.jsx, EditableTree (trong ExtractView.jsx), Dropzone/Icon/TagInput, style.css,
  pattern Inbox (lô + list + review full-width + SSE).
- Parsany BE: worker/queue.py+run_job.py, api/routes/{batches,events}, render 1 trang PNG.

## Phạm vi
- **MVP**: lô upload → extract → Mongo → bảng trích xuất → đối soát PDF↔GCN → hậu kiểm + đặt tên + tải.
- **Pha sau**: tiến độ %/từng bước + retry bước kẹt (engine đã có claim/retry → bề mặt hóa UI);
  cây lịch sử giấy (chuỗi Biến động/Số phát hành theo thời gian); dashboard.

## Verification (trên .199)
1. `cd /Users/bags/prj/ai-hub && docker compose up -d --build`; set VLLM_BASE_URL → model .199; tạo bucket.
2. Tạo việc: thả vài PDF GCN (gồm tờ bổ sung) → lô chạy live queued→processing→done.
3. Bảng trích xuất: danh sách GCN, gom/sắp theo Số phát hành; lọc/tìm.
4. Đối soát: bấm GCN → trái PDF, phải các trường khớp; sửa + đặt tên + Duyệt → PUT ghi Mongo; tải bộ.
5. Engine: `detect_and_extract` chạy trong worker (VLM .199), normalize đúng `Đăng ký → Số phát hành`.

## Nguồn tham chiếu
- Engine GCN: `/Users/bags/prj/auto-detect-extract-gcn-vlm/` (extract_path.py, src/extentions/multimodal/*).
- Pattern Parsany: `/Users/bags/prj/ai-parsany/` (frontend/src/components/{Inbox,ExtractView,PdfViewer,Dropzone}.jsx,
  src/parsany/{worker,api/routes,core/render}.py, docker-compose).
