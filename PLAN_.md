# Đưa cấu hình tỉnh (chi nhánh/branding) + nguồn S3 vào DB, quản lý qua Admin UI
> sobagi plan
## Quyết định thiết kế trọng yếu

1. **`branches.py` chuyển async, đọc từ `site_config`.** `is_valid_branch()`/`get_branches()` thành async; cập nhật cả 4 call site kể cả 2 script `backfill_branch.py:36`, `delete_branch.py:47` (đã là script async, chỉ thêm `await`). §Backend 1.
2. **Import thư mục lớn chạy qua hàng đợi worker** (doc `import_jobs`, worker claim như `gcns`) + SSE; tự resume sau restart nhờ stale-reclaim sẵn có. §Backend 4, §Vòng đời lô & import.
3. **Idempotency import** — partial unique index + skip-existing. §Mô hình dữ liệu, §Backend 4.
4. **Dangling reference** — file gốc mất ở nguồn: worker đưa doc→`error`, route xem/tải trả lỗi phân loại rõ (không 500 trần). §Backend 5,7.
5. **Cache client/config đã resolve** (nguồn theo id, đích singleton), invalidate khi mutation. §Backend 5.
6. **Chặn xóa `s3_connection` còn tham chiếu** (tránh doc mồ côi gốc). §Backend 3.
7. **Kho nguồn READ-ONLY tuyệt đối** ở mọi đường; `reset.py`/`delete_branch.py` chỉ thao tác `AIHUB_BUCKET`, không chạm nguồn. §Bất biến & an toàn dữ liệu.
8. **Vòng đời lô & import** — `batch` `importing→processing→done`, khôi phục sau restart, UI vừa SSE vừa poll. §Vòng đời lô & import.
9. **Seed/index vào `ensure_indexes()` + startup hook sẵn có** ([main.py:24-30]); upsert `$setOnInsert` an toàn đa-worker; partial index build nền. §Seed.
10. **Guard xóa/bớt branch còn tham chiếu**; validate `branches` không rỗng. §Backend 2.
11. **Edge case đã bao:** test-draft khi đang sửa (secret bị che); phân trang folder >1000 object; lọc `.pdf` + worker kiểm magic-byte; cùng file import sang 2 lô khác nhau (được phép). §Backend 3,4,6.
12. **Nhất quán cache đa-process:** TTL ~30s bắt buộc (event-invalidate chỉ là fast-path cục bộ). §Đồng thời.
13. **Quy mô chục triệu file (yêu cầu lõi):** cleanup streaming (không OOM), sharding+bulk import, SSE mức lô, counter duy trì thay `count_documents`, throughput GPU-bound scale ngang + observability, dead-letter poison doc, fairness lô. §Quy mô cực lớn.
14. **Vòng đời sau xử lý:** phát hiện nguồn đổi nội dung (etag), tra cứu-at-scale (cursor), hậu kiểm claim-to-review, audit truy cập dữ liệu nhạy cảm, export nền, scoping chi nhánh khi import, đánh dấu trùng nội dung. §Vòng đời sau xử lý.
15. **RBAC 3 role** (tách quyền config khỏi nghiệp vụ): `admin` (config/secret/users) · `operator` (import/quản lô/hậu kiểm, khóa chi nhánh, KHÔNG thấy secret) · `viewer` (tra cứu/xem, khóa chi nhánh). §Phân quyền.

## Context

Mỗi tỉnh chạy **một deployment riêng biệt** (docker-compose/container/DB riêng — đã tách ở tầng triển khai). **Không phải multi-tenant** — mỗi deployment chỉ cần cấu hình **của chính nó**.

Hiện cấu hình đó **hard-code trong source code** thay vì DB:
- Chi nhánh: hằng số `BRANCHES` ([branches.py:4]) — danh sách quận/huyện Hà Nội.
- Branding/copyright: chuỗi + logo hard-code trong JSX ([Login.jsx], [App.jsx:47-51]).
- Nơi lưu PDF: 1 bucket qua env tĩnh (`AIHUB_BUCKET`, `ENDPOINT_URL_MINIO`...), sửa phải đổi `.env` + build lại.

Mỗi tỉnh mới hiện phải **sửa code + build image riêng**. Mục tiêu: đưa toàn bộ vào MongoDB (đã là DB riêng/deployment), Admin UI tự sửa **không cần build lại**. Đồng thời: đọc PDF trực tiếp từ 1+ kho MinIO nguồn có sẵn (không copy gốc, chỉ lưu path), nguồn/đích cấu hình qua UI thay vì `.env`.

**Đã chốt**: mỗi deployment có **1 cấu hình tổ chức duy nhất**. Phân quyền theo **3 role** (tách quyền cấu hình khỏi quyền nghiệp vụ — xem §Phân quyền): mô hình 2-role cũ gộp "config + secret" với "nghiệp vụ" nên cấp quyền import buộc phải cấp cả secret → tách để tránh lộ.

## Phân quyền (RBAC — 3 role)

Mô hình 2-role cũ (`user`/`admin`) gộp **quyền hạ tầng/cấu hình** với **quyền nghiệp vụ**: `admin` vừa giữ secret S3 + đổi config toàn tỉnh, vừa là nơi duy nhất đủ quyền làm việc quản trị. Cấp quyền import cho cán bộ nhập liệu → buộc cấp `admin` → họ thấy secret + đổi được cả tỉnh (**lộ**). Tách thành 3 role:

| Role | Config/secret/users | Import từ nguồn | Hậu kiểm (review) | Tra cứu/xem/tải | Phạm vi |
|---|:---:|:---:|:---:|:---:|---|
| **admin** | ✓ | – | – | ✓ | toàn deployment |
| **operator** | – | ✓ | ✓ | ✓ | **khóa theo chi nhánh** |
| **viewer** | – | – | – | ✓ | **khóa theo chi nhánh** |

> Mặc định least-privilege: viewer chỉ tra cứu/xem/tải (kèm access-log), **không** review. Cần cho ai review thì cấp `operator` — không mở quyền lẻ, giữ mô hình đơn giản.

Nguyên tắc & ràng buộc:
- **Import KHÔNG cần thấy secret.** Browse/import tham chiếu connection theo `id`, server giữ secret, API không bao giờ trả secret ([§Backend 3]). Nên `operator` dùng được connection mà **không đọc được secret** → quyền config (secret/CRUD/test connection) vẫn **chỉ `admin`**.
- **Khóa chi nhánh cho operator/viewer:** browse nguồn + import + review + tra cứu đều giới hạn trong `branch` của user (admin không giới hạn). Chống cả lộ dữ liệu chéo chi nhánh lẫn gán nhầm nhãn branch.
- **Thay đổi kỹ thuật:** `deps.py` thêm role vào enum + JWT payload mang `role`; guard hiện có `is_admin`/`ensure_branch_access` bổ sung `require_operator`/`require_viewer` (kiểm cấp bậc role: admin > operator > viewer). Vì mỗi tỉnh = deployment riêng nên không cần chiều tenant, chỉ thêm chiều role.
- **Route map đầy đủ** (mọi route hiện có + mới đều phải gắn đúng cấp):
  - **admin:** `settings.py`, `s3_connections.py` (config/secret), `users.py` (quản tài khoản), đọc `audit_log` + access-log, quản dead-letter/retry.
  - **operator (branch-scoped):** `browse.py` + import, **`batches.py` (tạo lô/upload-từ-máy — hiện là `Depends(current_user)`, phải nâng lên operator)**, hậu kiểm (claim/submit review), **export** (xuất hàng loạt — nhạy hơn xem 1 doc, kèm access-log).
  - **viewer trở lên (branch-scoped):** tra cứu (`search`), xem trang/`download` 1 doc (kèm access-log), xem trạng thái lô.
- **Gán role khi tạo/sửa user:** dùng lại UI Users (`Users.jsx`) admin-only sẵn có — thêm chọn `role ∈ {admin, operator, viewer}` + chi nhánh (operator/viewer bắt buộc có branch hợp lệ; admin không cần). `users.py` validate role + branch.
- **Không nở thêm role.** 3 là đủ cho vòng đời hiện tại (config · nghiệp vụ · tiêu thụ); thêm nữa chỉ khi có nhu cầu thật.
- **Không cần migration role.** Chưa có dữ liệu người dùng thật → tạo mới với 3 role từ đầu, không phải backfill user cũ.

## Bất biến & an toàn dữ liệu (đọc trước khi code)

Đây là ràng buộc **không được vi phạm** — vi phạm = hỏng/mất dữ liệu gốc của khách hàng ở kho ngoài.

1. **Kho NGUỒN là READ-ONLY tuyệt đối.** Mọi đường code chạm nguồn chỉ được `head_bucket` / `list_objects_v2` / `get_object`. **Cấm** `put_object`/`delete_object`/`delete_objects`/tạo bucket trên nguồn — kể cả trong hàm test (`mode="source"` chỉ head+list). Chỉ `mode="destination"` mới được thử ghi (và phải xóa ngay file test của chính nó).
2. **File CẮT luôn ghi ĐÍCH, không bao giờ ghi nguồn.** `put_cut`/`put_pdf` chỉ đi tới client đích (destination-config hoặc fallback `AIHUB_BUCKET`).
3. **`reset.py` và `delete_branch.py` chỉ đụng ĐÍCH.** Đã kiểm: cả hai dùng `minio_client` + `config.AIHUB_BUCKET` ([reset.py:23], [delete_branch.py:_delete_minio]). Doc external có `s3_key` trỏ vào **nguồn**, nhưng các script này **không** dùng `s3_key` của doc để xóa — chúng liệt kê `AIHUB_BUCKET` theo tiền tố `batch_id/` (chỉ chứa **cuts**, luôn ở đích). ⇒ Chốt bất biến: **các script hủy dữ liệu chỉ được vận hành trên client đích, cấm mở session tới bất kỳ `s3_connections` role=source nào.** Thêm comment cảnh báo ngay tại 2 hàm xóa.
   **⚠ Ở quy mô chục triệu cut object:** hiện `delete_branch.py`/`reset.py` dùng `async_list_files()` **nạp toàn bộ key vào 1 list Python** ([minio_helper.py:155]) → **OOM**. Bắt buộc đổi sang **xóa streaming theo trang** (paginate list → `delete_objects` 1000/lần, không giữ toàn bộ key trong RAM) và làm resumable. Xem §Quy mô cực lớn.
4. **Xóa doc external không xóa file gốc.** Xóa 1 `gcn` external ở Mongo chỉ xóa bản ghi + cuts (ở đích); file gốc ở nguồn giữ nguyên (ta chỉ tham chiếu, không sở hữu).

## Mô hình dữ liệu mới (MongoDB — DB đã tách riêng/deployment)

`backend/app/db.py` thêm accessor theo pattern `batches()/gcns()/users()` ([db.py:17-26]); `config.py` thêm hằng số tên collection. Index + seed vào `ensure_indexes()` + startup hook sẵn có (§Seed).

- **`site_config`** — 1 doc singleton (`_id: "site"`): `{name, branches: [...], branding: {org_name, logo_url, copyright_text}}`. Seed lúc khởi động nếu chưa có, từ đúng nội dung hard-code hôm nay → Hà Nội chạy y hệt sau migrate.
- **`s3_connections`**: `{_id, role: "source"|"destination", name, endpoint_url, access_key_id, secret_access_key, bucket, verify_tls: false, created_at, updated_at, last_checked_at, last_check_status: "ok"|"error"|null, last_check_message}`. Nhiều `role="source"`; thường 1 `role="destination"` — chưa cấu hình thì **fallback `AIHUB_BUCKET`/`minio_client`** (không phá hành vi cũ).
- **`audit_log`** — mọi thay đổi cấu hình; không ghi nghiệp vụ thường ngày. §Audit log.
- **`import_jobs`** (mới) — 1 doc/lần import-thư-mục, worker claim y hệt `gcns`: `{_id, batch_id, source_connection_id, prefix, branch, status: "queued"|"processing"|"done"|"error", started_at, list_token, inserted, skipped, error, created_at}`. Đây là "job" của hàng đợi (Mongo-as-queue), không phải cấu hình → **không audit**. Xem §Vòng đời lô & import.
- **`gcns` — field mới trên doc external** (doc cũ không có → coi như internal): `source_connection_id: str` (trỏ `s3_connections._id`), `s3_key` = key gốc trong bucket nguồn (không đổi tên, không copy), `source_etag`/`source_mtime` (dấu vân nội dung lần import gần nhất — dùng phát hiện file nguồn đổi, xem §Vòng đời sau xử lý ①), `attempts: int` (đếm số lần xử lý — dead-letter, §Quy mô cực lớn 6).
- **Partial unique index (idempotency)** trên `gcns`: `{source_connection_id: 1, s3_key: 1, batch_id: 1}`, `partialFilterExpression: {source_connection_id: {$exists: true}}`, **build nền** (`background=True` — collection có thể đã lớn). Chỉ ràng buộc doc external; doc upload cũ không dính. Lưu ý: cùng 1 file **được phép** import sang **2 lô khác nhau** (khác `batch_id`) — đây là hành vi mong muốn (mỗi lô độc lập), index chỉ chặn trùng **trong cùng 1 lô**.

## Seed & index — hook vào startup sẵn có (an toàn đa-worker)

`main.py` đã có `@app.on_event("startup")` gọi `ensure_indexes()` + `_seed_admin()` ([main.py:24-30]). Thêm vào đúng chỗ đó:
- `ensure_indexes()`: thêm `create_index` cho `audit_log` (`at` desc, `actor`, `action`, `target`), partial unique index `gcns` (background), và `import_jobs` (`status`, `started_at` — cho claim/stale-reclaim). `createIndex` idempotent → nhiều API worker gọi song song không sao.
- `_seed_site_config()`: **upsert `$setOnInsert`** doc `_id:"site"` với nội dung hard-code hiện tại → chạy đồng thời nhiều worker vẫn chỉ tạo 1 lần, không đè cấu hình admin đã sửa. (Cùng pattern `_seed_admin` đang dùng.)

## Audit log — chi tiết đầy đủ

### Schema (`backend/app/audit.py`)
```python
{
  "_id": ObjectId(),                       # cursor phân trang
  "at": datetime.utcnow(),                 # UTC, index
  "actor": "admin1",                       # username JWT; hoặc "agent:<user>" khi qua agent (§Khảo sát)
  "action": "site_config.update",          # enum cố định
  "target": "site",                        # "site" | s3_connection._id
  "detail": {"before": {...}, "after": {...}},
}
```
Index: `at` (desc), `actor`, `action`, `target`.

### `action` — enum cố định
```python
class AuditAction(str, Enum):
    SITE_CONFIG_UPDATE   = "site_config.update"
    S3_CONNECTION_CREATE = "s3_connection.create"
    S3_CONNECTION_UPDATE = "s3_connection.update"
    S3_CONNECTION_DELETE = "s3_connection.delete"
    S3_CONNECTION_TEST   = "s3_connection.test"   # chỉ test connection ĐÃ LƯU
```

### `detail` — không bao giờ chứa secret thật
- `site_config.update`: `before`/`after` là field thực đổi trong `{name, branches, branding}` (diff nông theo key; `branches` đổi → log cả mảng).
- `s3_connection.*`: **luôn thay `secret_access_key`=`"<redacted>"` nếu có, hoặc bỏ field nếu rỗng**. Field khác nguyên văn.
- `s3_connection.test`: `detail = {"result": "ok"|"error", "message": "..."}`.
- `s3_connection.delete`: `detail = {"deleted": {..., "secret_access_key": "<redacted>"}}`.

### Test trên draft KHÔNG ghi log
`POST /v1/s3-connections/test` (chưa lưu) không ghi — chưa có `target`, chưa đổi dữ liệu. Chỉ `.../{id}/test` mới ghi.

### Bất biến: append-only
Không `PATCH`/`DELETE` route, không nút xóa — kể cả admin. Dọn dữ liệu cũ là thao tác trực tiếp trên Mongo, không lộ qua API.

### API đọc — filter + phân trang
```
GET /v1/audit-log?action=&actor=&target=&from=&to=&limit=50&before_id=<cursor>
```
Trả `{items: [...], next_cursor: <_id cuối|null>}`. admin-only.

### UI — tab "Audit log" (§Chuẩn UI dense)
Bảng 1 dòng/record: **Thời gian** (tương đối, hover→ISO) · **Actor** · **Hành động** (badge màu theo nhóm, dùng lại `.badge`) · **Đối tượng** · "Xem chi tiết" (accordion trong-dòng before/after). Lọc: dropdown action (enum), input actor, date range.

## Backend — các thay đổi chính

### 1. `backend/app/branches.py` — async, đọc `site_config`, có cache
```python
async def get_branches() -> list[str]          # site_config().branches, qua cache
async def is_valid_branch(name: str | None) -> bool
def invalidate_site_cache() -> None            # gọi sau PATCH /settings/site
```
- **Cache trong tiến trình có TTL bắt buộc ~30s** (singleton đổi hiếm) → không 1 read/validate. TTL là bắt buộc, không chỉ event-invalidate: API chạy nhiều process/worker, `invalidate_site_cache()` chỉ xóa cache của process nhận request → các process khác phải tự hết hạn sau ≤TTL mới hội tụ. `PATCH /settings/site` gọi `invalidate_site_cache()` như fast-path cục bộ. Xem §Đồng thời.
- **Cập nhật MỌI call site** (đều async, thêm `await`): `users.py:39`; `batches.py:49` (validate) & `:126` (`GET /branches` trả `await get_branches()`); **`backfill_branch.py:36`** và **`delete_branch.py:47`** (cả 2 đã `async def main` + có `app.db` → chỉ thêm `await`).

### 2. Router mới `backend/app/routes/settings.py` (1 cấu hình/deployment)
- `GET /v1/settings/site` (admin) — đầy đủ `site_config`.
- `PATCH /v1/settings/site` (admin) — sửa `name`/`branches`/`branding`; ghi audit; `invalidate_site_cache()`. **Validate:**
  - `branches` **không rỗng** (rỗng → user không gán được chi nhánh, chặn với 400).
  - **Bớt/đổi tên một branch còn tham chiếu:** nếu branch bị xóa khỏi list mà `users`/`batches`/`gcns` còn dùng → trả cảnh báo kèm số bản ghi ảnh hưởng; chỉ áp dụng khi client gửi `confirm=true` (UI hiện hộp xác nhận "N tài khoản / M lô đang dùng chi nhánh này"). Song song với guard xóa connection (§3), tránh làm dữ liệu mồ côi chi nhánh âm thầm.
- `GET /v1/settings/branding` — **public, không auth** (Login cần trước đăng nhập) — chỉ `{org_name, logo_url, copyright_text}` (không field nhạy cảm).

### 3. Router mới `backend/app/routes/s3_connections.py` (admin-only)
- `GET ?role=source|destination` — list, **che secret** (chỉ `secret_set: bool`, không bao giờ trả secret).
- `POST` / `PATCH /{id}` — CRUD. PATCH: **secret để trống = giữ giá trị cũ** (không ghi đè bằng rỗng). Mutation → `storage.invalidate_s3_cache()` (§5) + ghi audit.
- `DELETE /{id}` — **chặn nếu còn tham chiếu**: `gcns.count_documents({source_connection_id: id}) > 0` → `409` + số doc; chỉ xóa khi `?force=true` (UI xác nhận, cảnh báo doc sẽ mồ côi gốc — không đọc lại được). Ghi audit.
- `POST /v1/s3-connections/test` — test cấu hình **chưa lưu** (draft). **Edge case sửa connection:** nếu draft đến từ form sửa và secret bị che (người dùng không nhập lại) → body gửi cờ `use_stored_secret: true` + `id`, server lấy secret đã lưu để test; nếu tạo mới thì bắt buộc có secret trong body. KHÔNG ghi audit.
- `POST /v1/s3-connections/{id}/test` — test đã lưu (dùng secret đã lưu); cập nhật `last_checked_at/last_check_status/last_check_message`; ghi audit.
- Helper `backend/app/audit.py::log_action(actor, action, target, detail)`.

### 4. Router mới `backend/app/routes/browse.py` (operator trở lên, khóa theo chi nhánh — §Phân quyền)
- `GET /v1/browse/sources` — list `s3_connections` role=source (`id`, `name`).
- `GET /v1/browse/{source_id}?prefix=&token=` — duyệt **1 cấp** (folders+files) qua `async_list_folder` (§6); trả `next_token` để UI "tải thêm" khi folder >1000 object.
- `POST /v1/browse/{source_id}/import` — body `{prefix?, recursive, keys?, branch, batch_id?}`. Doc `gcn` external tạo giống [batches.py:65-80] nhưng **giữ nguyên `s3_key` gốc**, thêm `source_connection_id`, `source_etag/source_mtime`, `page_count=0` (worker tự đếm — [run_job.py:109,224]), `status="queued"`; gom vào `batch` như [batches.py:95-102].
  - **Quyền:** router `browse.py` yêu cầu **operator trở lên** (§Phân quyền), KHÔNG phải admin. `operator` bị **khóa theo chi nhánh**: chỉ browse/import nguồn↔prefix thuộc chi nhánh mình, doc tạo ra mang đúng branch đó (admin không giới hạn). Chống cả import chéo lẫn gán nhầm nhãn branch. Map nguồn/prefix↔chi nhánh khai trong `s3_connections` hoặc `site_config`.
  - **Hai chế độ:**
  - **`keys` (chọn lẻ, số ít):** **đồng bộ trong request**. Trả `{created, skipped}`.
  - **`prefix`+`recursive` (cả thư mục, có thể rất nhiều):** endpoint **chỉ chèn 1 doc `import_jobs` `status="queued"`** + đặt `batch.status="importing"` rồi trả **ngay** `{batch_id, status:"importing"}`. **Không** làm việc nặng trong request. Việc liệt kê+insert do **worker** làm (§4b) — sống sót qua restart API vì job nằm trong Mongo, không phải bộ nhớ tiến trình API.
  - **Idempotency (cả 2):** insert vướng partial unique index → **skip-existing** (bắt `DuplicateKeyError`, đếm `skipped`). Chọn lại cùng thư mục không nhân đôi.
  - **Lọc `.pdf`**: theo đuôi **không phân biệt hoa-thường** ở bước liệt kê (rẻ). Worker khi xử lý **kiểm magic-byte**; không phải PDF → doc `status="error"` message rõ (bắt cả file sai đuôi lẫn `.pdf` hỏng).
  - Không tải bytes gốc ở bước tạo lô (cả 2 chế độ) — chỉ ghi Mongo.

### 4b. `backend/app/worker/main.py` — worker claim thêm `import_jobs`
Worker hiện là **Mongo-as-queue**: `_claim()` atomic `find_one_and_update` doc `gcns` `status="queued"` (hoặc `processing` treo quá `PROC_TTL` → **tự reclaim sau crash**) ([worker/main.py:24-37]). Không có queue ngoài. Mở rộng đúng idiom đó:
- Thêm `_claim_import_job(mongo)` — cùng mẫu atomic claim + stale-reclaim (`status="queued"` hoặc `processing` cũ hơn `PROC_TTL`) trên `import_jobs`. Ưu tiên claim ở đầu vòng `run()` (rẻ, ít doc), rồi mới claim `gcns` như cũ; hoặc chạy 1 coroutine poll riêng nhẹ trong cùng process — miễn không chặn pool xử lý PDF.
- `process_import_job(mongo, job)`: **stream** `async_list_files_paginated(bucket, prefix, suffix_filter=".pdf")` theo trang (KHÔNG gom hết vào RAM); mỗi chunk (~500): **`insert_many(..., ordered=False)`**, đếm trùng từ `BulkWriteError` (dup key) thay vì chèn từng cái bắt `DuplicateKeyError` — ở chục triệu file, khác biệt là vài nghìn round-trip so với hàng chục triệu. `$inc batch.file_count` số thực chèn, cập nhật `import_jobs.{inserted,skipped,list_token}`, **SSE mức lô** (đếm định kỳ, không per-doc — §Quy mô cực lớn). Xong: `batch.status="importing"→"processing"`, `import_jobs.status="done"`.
- **Sharding (bắt buộc ở quy mô lớn):** với prefix khổng lồ, tách 1 "planner job" liệt kê các sub-prefix cấp 1 rồi sinh **nhiều `import_jobs` con** (mỗi sub-prefix 1 job) → nhiều worker nạp song song thay vì 1 job serial. Xem §Quy mô cực lớn.
- **Phát hiện nguồn đổi nội dung:** khi liệt kê gặp key **đã có** doc nhưng `etag`/`last_modified` **khác** `source_etag/source_mtime` đã lưu → không skip mà **đặt lại `status="queued"`** + cập nhật etag/mtime mới (xử lý lại bản mới). Key trùng **và** etag khớp mới thực sự skip. Xem §Vòng đời sau xử lý ①.
- **Heartbeat:** mỗi chunk cập nhật `import_jobs.started_at = now` (bump) → job **đang khỏe** không bị reclaim nhầm giữa lúc liệt kê lâu; chỉ job **thực sự treo** (worker chết, hết bump) mới quá `PROC_TTL`. Kể cả nếu 2 worker lỡ cùng chạy 1 job, unique index chặn double-insert **và** double-count `file_count` (chỉ insert thành công mới `$inc`).
- **Tự resume (không cần code resume riêng):** worker chết giữa chừng → job kẹt `processing`, hết bump, quá `PROC_TTL` bị **reclaim** và chạy lại. An toàn nhờ **idempotency** (partial unique index → chèn lại chỉ thêm phần thiếu). Lưu `list_token` để lần chạy lại **tiếp gần chỗ dừng** thay vì liệt kê lại từ đầu (tối ưu; kể cả liệt kê lại toàn bộ vẫn đúng vì skip-existing).
- Lỗi không phục hồi (nguồn chết hẳn, prefix sai): sau vài lần reclaim thất bại → `import_jobs.status="error"` + `error`, `batch.import_status="failed"`; **giữ nguyên** doc đã chèn (chúng xử lý bình thường).

### 5. `backend/app/storage.py` — đọc/ghi động, cache client
```python
async def get_pdf(key, source_connection_id: str | None = None) -> io.BytesIO
async def render_page(key, page_index, width, source_connection_id=None) -> bytes
async def put_pdf(key, data) -> None    # web-upload gốc — luôn ghi ĐÍCH
async def put_cut(key, data) -> None    # cuts — luôn ghi ĐÍCH
def invalidate_s3_cache() -> None       # gọi từ §3 sau mutation
```
- `source_connection_id=None` → `minio_client`/`AIHUB_BUCKET` như hôm nay (doc cũ y nguyên).
- Có id → client từ **cache** (dict theo id; miss thì tra `s3_connections`, build client runtime §6, cache). **Read-only nguồn.**
- `put_pdf`/`put_cut` → **client đích đã cache** (tra role=destination 1 lần rồi cache; chưa có → fallback `AIHUB_BUCKET`). Không tra Mongo mỗi lần ghi (worker cắt nhiều cut/doc).
- Cache client cũng **TTL ~30s bắt buộc** (cùng lý do đa-process ở §Backend 1). `invalidate_s3_cache()` là fast-path cục bộ khi mutation `s3_connections`; process khác hội tụ sau ≤TTL. Xem §Đồng thời.
- **Dangling:** bắt lỗi client (`NoSuchKey/404/NoSuchBucket/AccessDenied`) → raise `SourceObjectUnavailable(message)` thay vì lỗi trần; người gọi xử lý (§7).

### 6. `backend/src/extentions/minio_helper.py` (vendor) — KHÔNG sửa
Dựng client runtime **không đụng vendor** (an toàn hơn patch constructor; mọi method mở session mới đọc `self.xxx`):
```python
def _build_client(conn: dict) -> MinioClient:
    c = MinioClient()                       # no-arg (call site duy nhất kiểu này)
    c.endpoint_url        = conn["endpoint_url"]
    c.aws_access_key_id   = conn["access_key_id"]
    c.aws_secret_access_key = conn["secret_access_key"]
    c.verify = bool(conn.get("verify_tls"))  # mặc định False như nội bộ; bật cho nguồn internet
    return c
```
> Constructor vendor **nuốt tham số** ([minio_helper.py:57-69]) nên KHÔNG dùng `MinioClient(endpoint,...)`; né bằng gán thuộc tính sau khởi tạo, không cần fix vendor.

Hai hàm **mới** đặt ở lớp app (`backend/app/s3_util.py`, raw `aioboto3` theo pattern scripts) — KHÔNG sửa vendor:
- `async_list_folder(conn, prefix="", delimiter="/", token=None) -> (folders, files, next_token)` — `list_objects_v2(Delimiter="/")`, đọc `CommonPrefixes` + `Contents` (lọc `.pdf` không phân biệt hoa-thường, kèm size/last_modified), trả `next_token` phân trang. Vendor chỉ có hàm liệt kê đệ quy, không hỗ trợ `Delimiter`.
- `async_test_connection(endpoint, access_key, secret_key, bucket, verify, mode) -> dict` — **viết mới** (đoạn phân loại lỗi trong vendor là demo chết trong `if __name__=="__main__"` [minio_helper.py:339], KHÔNG dùng lại). Phân loại: `EndpointConnectionError/ConnectTimeoutError`→endpoint/mạng; `InvalidAccessKeyId`→sai access key; `SignatureDoesNotMatch`→sai secret/lệch giờ; `404/NoSuchBucket`→bucket không tồn tại; `AccessDenied`→thiếu quyền. `mode="destination"`: thử `put_object`+xóa 1 file test. `mode="source"`: chỉ `head_bucket`+`list_objects_v2(MaxKeys=1)`, **cấm ghi/xóa** (§Bất biến 1).

### 7. `backend/app/worker/run_job.py` + `backend/app/routes/gcn.py`
- `run_job.py:206` (`process_doc`): → `storage.get_pdf(doc["s3_key"], doc.get("source_connection_id"))`. **Bắt `SourceObjectUnavailable`** → doc `status="error"`, `error="Không đọc được file gốc từ kho nguồn: <msg>"` (không kẹt `processing`). Thêm **kiểm magic-byte** ngay sau khi đọc: không phải PDF → `error` message rõ.
- `run_job.py:204` (`_build_cuts`): `put_pdf` → `put_cut`.
- `gcn.py:197` (`get_page`), `:309` (`download`): projection thêm `source_connection_id`, truyền xuống `render_page`/`get_pdf`; **bắt `SourceObjectUnavailable`** → `502`/`409` message rõ, không 500 trần. `gcn.py:227` (`cut_page`): **không đổi** (cuts luôn `source_connection_id=None`).

## Vòng đời lô & import

**Import thư mục chạy qua worker-queue (doc `import_jobs`).** Worker đã là Mongo-as-queue với stale-reclaim → đặt job vào Mongo là tự nhiên, sống sót qua restart API/worker, **resume rơi ra miễn phí** từ reclaim + idempotency. Không thêm hạ tầng mới.

Trạng thái `batch`:
- **Upload từ máy / import `keys` (đồng bộ):** như hôm nay → `processing` ngay khi có doc.
- **Import thư mục:** API đặt `importing` + chèn `import_jobs` queued → worker liệt kê+insert (§4b) → `importing→processing` khi job xong. Worker xử lý các doc `gcn` `queued` song song ngay khi chúng xuất hiện, không đợi liệt kê xong.
- Về `done`/`error` theo logic tổng hợp hiện có (không đổi).

Chống kẹt + khôi phục (đều nhờ cơ chế sẵn có, không code riêng):
- **Worker/API restart giữa import:** `import_jobs` kẹt `processing` → quá `PROC_TTL` bị **reclaim**, chạy tiếp; idempotency (partial unique index) đảm bảo không nhân đôi, `list_token` giúp tiếp gần chỗ dừng.
- **Lỗi không phục hồi:** sau vài lần reclaim thất bại → `import_jobs.status="error"`, `batch.import_status="failed"`; doc đã chèn giữ nguyên, xử lý bình thường; batch không kẹt `importing` vĩnh viễn.

UI:
- **Vừa SSE vừa poll:** browser không kết nối lúc import (hoặc reload) sẽ lỡ SSE → khi mount/`importing` thì **refetch batch** để lấy `file_count` hiện tại, không chỉ dựa SSE.

## Đồng thời & tải nhiều user

### An toàn theo thiết kế (không cần thêm gì)
- **Hàng đợi worker:** claim atomic `find_one_and_update` ([worker/main.py:24-37]) → mỗi doc chỉ 1 worker xử lý dù nhiều worker/nhiều user, không double.
- **Idempotency import đồng thời:** nhiều user import trùng key vào cùng lô → partial unique index bắt `DuplicateKeyError` (skip); `$inc file_count` **chỉ khi insert thành công** → đếm đúng kể cả khi đua.
- **SSE:** `bus.py` dùng **Redis pub/sub** (không in-process) → nhiều user và **nhiều API process** đều nhận event đúng. Không có vấn đề fan-out.

### Nhất quán cache qua nhiều API process (BẮT BUỘC xử lý)
Cache `site_config` và cache S3-client/config sống **trong từng process API**. `invalidate_*()` chỉ xóa cache của process nhận request → process khác giữ giá trị cũ. Ca nguy hiểm: admin **đổi secret** S3 nguồn → process khác vẫn dùng creds cũ, đọc file external **fail 403** vô thời hạn nếu chỉ event-invalidate.
→ **Chốt: mọi cache cấu hình dùng TTL ~30s** (§Backend 1, §Backend 5). Event-invalidate giữ làm fast-path cục bộ; TTL bảo đảm mọi process hội tụ sau ≤30s. Deployment 1 admin/tỉnh, config đổi hiếm → 30s cũ chấp nhận được.

### Tải (không phải lỗi đúng-đắn, cần biết)
- **Không fairness trong queue:** 1 user import thư mục rất lớn → queue đầy doc của họ, doc user khác xếp sau (claim ~FIFO theo thứ tự insert). Kết quả đúng, chỉ **trễ** cho người khác. Trần thông lượng thực là `MAX_VLM_CONCURRENT` (vLLM). Ở quy mô chục triệu file điều này thành vấn đề thật (lô mới đói vô hạn sau lô khổng lồ) → cần priority/round-robin theo lô. Xem §Quy mô cực lớn.
- **`render_page` (PDF→PNG) nặng CPU trong process API:** nhiều user cùng xem trang → CPU/event-loop căng. Đã có `Cache-Control` (private max-age) giảm tải lặp. Pre-existing.
- **`aioboto3.Session()` tạo mới mỗi call** (vendor + hàm mới) → nhiều user cùng đọc file external = nhiều kết nối ngắn tới MinIO nguồn (thêm áp lực fd/kết nối so với chỉ 1 đích nội bộ). Pre-existing pattern; giữ nguyên ở bản này, ghi nhận là điểm cần theo dõi nếu đồng thời đọc external cao.

### Ghi chú nhỏ
- **2 admin sửa config cùng lúc:** last-write-wins trên PATCH (hiếm — 1 admin/deployment); audit log ghi cả hai. Chấp nhận được, không thêm optimistic-lock.
- **Xóa connection khi import đang chạy:** guard `DELETE` đếm `gcns` tham chiếu; nếu muốn chặt hơn có thể đếm thêm `import_jobs` đang `processing` cùng `source_connection_id`. Nếu vẫn force-delete: worker mất nguồn → job/doc `error` graceful (§Backend 4b/7), không hỏng dữ liệu.

## Quy mô cực lớn (chục triệu file — yêu cầu lõi, không phải edge case)

Tổng khối lượng cần xử lý có thể tới **hàng chục triệu file**. Ở quy mô này, ingest **không được crash** (đã đạt nhờ worker-queue), nhưng vài chỗ chuyển từ "chậm" sang "vỡ" nếu không siết. Nguyên tắc bao trùm: **không thao tác nào được O(tổng-số-object) trong RAM; mọi thứ stream/phân trang; ingest tách rời processing để scale ngang.**

### 1. Cleanup không được OOM (bắt buộc)
`delete_branch.py`/`reset.py` hiện `async_list_files()` nạp **toàn bộ** key vào 1 list ([minio_helper.py:155]) → chục triệu cut object = chết RAM. Đổi sang:
- **Xóa streaming:** paginate `list_objects_v2` → gom mỗi 1000 key → `delete_objects` → **bỏ**, không giữ toàn bộ trong RAM.
- **Resumable:** thao tác xóa lớn (hàng giờ) nên là **job có thể chạy lại** (giống import_jobs), không phải script CLI mong manh dễ bị kill giữa chừng. Chạy lại chỉ xóa phần còn lại (list luôn trả cái chưa xóa).

### 2. Import: sharding + bulk (bắt buộc)
- **Sharded planner→child jobs** (§4b): 1 planner liệt kê sub-prefix cấp 1, sinh nhiều `import_jobs` con → N worker nạp song song. 1 job serial liệt kê chục triệu key là điểm nghẽn + điểm chết đơn.
- **`insert_many(ordered=False)`** theo chunk, đếm trùng từ `BulkWriteError` (§4b).
- **Ngưỡng cảnh báo:** prefix vượt ngưỡng (vd 100k file) → UI cảnh báo + tự bật chế độ sharding.

### 3. SSE gộp mức lô (bắt buộc)
Bắn 1 event/doc cho chục triệu doc = bão Redis + đơ browser. Với lô lớn:
- Worker/agg publish **event mức lô định kỳ** (mỗi vài giây hoặc mỗi N doc): `{batch_id, counts:{queued,processing,done,error}, file_count}` — KHÔNG per-doc.
- UI vẽ thanh tiến độ + số đếm từ event gộp; muốn xem chi tiết 1 doc thì query riêng, không stream tất.

### 4. Đếm trạng thái bằng counter duy trì, KHÔNG `count_documents` (bắt buộc)
`count_documents({batch_id, status})` trên chục triệu doc = quét đắt, gọi lặp trên UI càng chết. Duy trì **counter trên doc `batch`**: `counts.{queued,processing,done,error}`, `$inc` **nguyên tử tại mỗi lần chuyển trạng thái** (worker cập nhật khi doc đổi status). UI/stats đọc counter, không đếm live. Số này cũng là nguồn cho SSE mức lô (§3).

### 5. Throughput là GPU-bound — thiết kế phải chấp nhận & đo được
Phép tính trần: ~1 phút/file × 8 luồng/node ⇒ 1 node ≈ **~11.5k file/ngày**. Chục triệu file ⇒ **nhiều node-tháng**. Kết luận:
- **Không nhồi tất cả vào "phải xong ngay".** Queue bền trong Mongo → **scale tuyến tính**: thêm worker/GPU node là tăng thông lượng. Ingest (nhẹ) tách hẳn processing (nặng) — đây là lý do kiến trúc queue.
- **Bắt buộc có observability:** tốc độ xử lý (doc/phút), tồn đọng (queued), ETA, tỉ lệ error — từ counter (§4). Không có cái này thì vận hành mù ở quy mô lớn.
- **Backpressure nguồn:** đọc file gốc từ MinIO nguồn ở tốc độ xử lý — không prefetch ồ ạt (mỗi call mở session mới, §Đồng thời) — tránh dội nguồn.

### 6. Lỗi ở quy mô lớn — không chặn, truy được, retry hàng loạt
Chục triệu file thì kể cả 1% lỗi = hàng trăm nghìn doc `error` (PDF hỏng, không phải PDF, nguồn timeout). Cần:
- Lỗi 1 doc **không chặn** doc khác (đã có — mỗi doc độc lập).
- **Query lỗi hàng loạt** theo lô/loại lỗi (index `status`), và thao tác **"retry tất cả error"** (đặt lại `status=queued` theo bộ lọc) — không sửa tay từng doc.
- Phân biệt lỗi **tạm thời** (nguồn timeout — nên auto-retry vài lần) vs **vĩnh viễn** (không phải PDF — đừng retry vô ích); lưu `error_kind` để lọc.
- **Dead-letter cho "poison doc" (bắt buộc):** doc làm worker **chết cứng** (render treo/segfault/OOM — KHÔNG phải exception bắt được) sẽ bị stale-reclaim nhặt lại **vô hạn** → 1 doc độc bào mòn cả pool; ở chục triệu file chắc chắn gặp. Mỗi lần claim `$inc attempts`; vượt ngưỡng (vd 3) → chuyển `status="dead"` (ngừng reclaim), ghi `error_kind`, để ops soi/retry thủ công riêng. Không có cơ chế này thì cả pool có thể kẹt quanh vài doc độc.

### 7. Fairness giữa các lô
Claim ~FIFO → lô mới đói vô hạn sau lô chục triệu. Thêm cơ chế công bằng: **round-robin theo `batch_id`** khi claim (hoặc field `priority` trên doc/lô). Để một lô khẩn nhỏ vẫn chen được giữa lô khổng lồ.

### 8. Sizing (ghi chú vận hành)
- **Mongo:** chục triệu doc metadata nhỏ nhưng index (`status`, `batch_id`, `branch`, partial unique) tốn RAM/disk đáng kể — cần đủ RAM cho working set index, nếu không claim/query chậm dần. Theo dõi kích thước index.
- **MinIO đích:** chục triệu×N cut object — **không bao giờ list-all**; xóa luôn theo prefix `batch_id/`/`gcn_id/` qua paginator (§1).
- **Review UI:** không render cả lô — luôn phân trang + lọc + ảo hóa (§Frontend, §Chuẩn UI dense).

## Vòng đời sau xử lý (tra cứu · hậu kiểm · export · audit truy cập)

Nửa vòng đời phía **người tiêu thụ kết quả** (không phải người nạp). Với sản phẩm KB, đây là phần tạo giá trị — cần thiết kế ngang tầm phần nạp.

### ① Nguồn đổi nội dung → xử lý lại (đã gắn vào §Mô hình dữ liệu + §Backend 4)
Skip-existing theo `s3_key` sẽ **vĩnh viễn bỏ qua** file gốc bị quét lại/sửa nội dung nhưng giữ key. Lưu `source_etag/source_mtime`; import gặp key trùng nhưng etag khác → re-queue thay vì skip. Không có cái này, đồng bộ tăng dần cho ra bản trích cũ sai.

### ② Tra cứu / tìm kiếm ở quy mô lớn (omission lõi — bổ sung)
Code đã có `search_gcn` + index `extracted_so_phat_hanhs`, nhưng chưa thiết kế cho chục triệu doc. Chốt:
- **Tra theo field có index:** số phát hành, chi nhánh, trạng thái, khoảng thời gian, (tùy) tên chủ sử dụng. Compound index theo mẫu truy vấn thực (vd `{branch:1, created_at:-1}`, `{extracted_so_phat_hanhs:1}`).
- **Phân trang cursor** (theo `_id`/`created_at`), **không** `skip/limit` sâu (skip lớn = quét đắt ở chục triệu).
- **Full-text tiếng Việt** (tên/địa chỉ) là quyết định riêng: MongoDB text index có giới hạn tiếng Việt (dấu) → nếu cần tìm mờ theo tên, cân nhắc chuẩn hóa bỏ dấu vào field phụ để index, hoặc công cụ search chuyên (ngoài phạm vi bản này, nhưng **phải quyết trước khi hứa tính năng tìm theo tên**).
- **Đọc từ counter/aggregate duy trì**, không đếm live (§Quy mô cực lớn 4).
- API: `GET /v1/gcn/search?...` cursor-based; trả field tóm tắt, không nhồi cả bản trích.

### ③ Hậu kiểm (review) có tổ chức ở quy mô lớn (bổ sung)
Doc có `review{status,reviewer,at}` nhưng chưa có workflow phân việc. Ở chục triệu doc cần:
- **Hàng chờ theo phạm vi:** lọc theo chi nhánh/độ ưu tiên/độ tin cậy trích (doc trích "ngờ" ưu tiên duyệt trước).
- **Counter duy trì** `reviewed/unreviewed` mức lô + chi nhánh (không `count_documents`).
- **Bất biến:** sửa của reviewer ghi vào field riêng, không đè dữ liệu trích gốc; ghi ai/khi (audit nghiệp vụ nhẹ).

**Đồng thời trên CÙNG 1 file (bắt buộc xử lý) — 2 đường dẫn tới cùng doc:**
1. **Qua hàng chờ:** "lấy việc" gán doc atomic → 2 người **không thể được phát trùng** (dưới).
2. **Mở thẳng** (qua search/URL/lô): 2 người vẫn có thể cùng mở 1 doc dù không qua hàng chờ. Đây là case phải khóa ở tầng doc, không chỉ tầng hàng chờ.

Cơ chế soft-lock trên chính doc (`review.lock = {by, at, expires_at}`):
- **Acquire khi mở** màn hậu kiểm: `find_one_and_update` **atomic** đặt lock nếu (chưa có lock) HOẶC (lock đã hết hạn) HOẶC (lock của chính mình). Ai thắng thì sửa được; người khác mở cùng lúc thấy **read-only + "Đang được `X` hậu kiểm"**, có nút "tiếp quản" chỉ khi lock đã hết hạn.
- **Heartbeat** khi đang sửa (bump `expires_at`, vd TTL 2–5 phút) → không bị người khác tiếp quản giữa chừng; đóng tab/crash → lock tự hết hạn, doc trả lại.
- **Optimistic concurrency khi lưu (chốt chặn cuối):** doc mang `review.version` (hoặc `updated_at`); submit kèm version đã đọc → server `update` có điều kiện `version khớp`. **Lệch → 409 "người khác vừa sửa, tải lại"**, KHÔNG last-write-wins (mất sửa thầm lặng là điều tối kỵ trong hậu kiểm). Kể cả 2 người lách được lock (2 tab, lock hết hạn đúng lúc), version-check vẫn chặn ghi đè mù.
- **"Lấy việc" hàng loạt** vẫn atomic như cũ (gán N doc `unreviewed` → `review.reviewer` + lock), tránh phát trùng; nhưng lock/version ở trên mới là thứ bảo vệ khi **mở thẳng** không qua hàng chờ.

### ④ Audit truy cập dữ liệu nhạy cảm (bổ sung)
Dữ liệu đất đai + chủ sử dụng = **dữ liệu cá nhân**. Audit hiện chỉ ghi thay đổi **cấu hình**; **xem/tải bản gốc** của công dân nào **không** được ghi. Hệ thống nhà nước thường yêu cầu audit truy cập. Bổ sung **access-log** (tách khỏi `audit_log` cấu hình để không loãng): ghi `download`/xem gốc external (`actor`, `gcn_id`, `at`, `action: view|download`). Cân nhắc lấy mẫu/tổng hợp nếu lượng xem lớn, nhưng `download` gốc thì nên ghi đủ.

### ⑤ Scoping chi nhánh khi import nguồn (giải quyết bằng §Phân quyền + §Backend 4)
`operator` (không phải admin) làm import, **khóa theo chi nhánh** → không import chéo, không gán nhầm branch. Quyền config/secret vẫn chỉ admin. Xem §Phân quyền.

### ⑥ Dead-letter cho poison doc (đã gắn vào §Quy mô cực lớn 6)
`attempts` + chuyển `status="dead"` sau N lần để 1 doc độc không giết pool.

### ⑦ Export / bàn giao kết quả (bổ sung)
Sau trích + duyệt cần xuất (nộp hồ sơ thầu / đẩy hệ hạ nguồn). `GET /v1/gcn/export?filter=...` **streaming** (CSV/NDJSON, cursor-based, không load-all) theo lô/chi nhánh/khoảng thời gian; ở quy mô lớn export nên là **job nền** (giống import_jobs) ghi file ra bucket đích rồi trả link tải, không giữ trong RAM/timeout request. **Quyền: operator trở lên, khóa chi nhánh, ghi access-log** (xuất hàng loạt nhạy hơn xem lẻ — §Phân quyền, §④).

### ⑧ Trùng nội dung khác đường dẫn (ghi nhận)
Unique index chống trùng theo **key**, không theo **nội dung** — cùng 1 GCN scan 2 lần dưới 2 tên → 2 doc, trùng trong KB. Tối thiểu **đánh dấu nghi trùng** sau khi trích (theo `số phát hành`/content-hash), không âm thầm nhân đôi; dedup thật sự cân nhắc sau. Không chặn cứng vì 2 bản scan có thể khác chất lượng.

## Frontend

### 1. Branding động — thay hard-code [Login.jsx], [App.jsx:47-51]
Gọi `GET /v1/settings/branding` (không token) lúc mount; thay chuỗi/`logo-sotnmt.png` cứng bằng giá trị fetch (fallback giá trị hiện tại nếu API lỗi).

### 2. Chọn dữ liệu từ MinIO — song song `CreateBatch.jsx`
Toggle "Từ máy tính" / "Từ kho MinIO". MinIO: dropdown nguồn (`getBrowseSources()`), component mới `MinioBrowser.jsx` (breadcrumb + list folder/file, click đi sâu, checkbox lẻ / "chọn cả thư mục", **"tải thêm"** khi `next_token`) — §Chuẩn UI dense. Submit `importFromMinio({sourceId, prefix, recursive, keys, branch, batchId})`.
- **`keys` (đồng bộ):** spinner → hiện `{created, skipped}`.
- **Thư mục (nền):** nhận `importing` + `batch_id` ngay → "Đang nhập…", lắng SSE để `file_count` tăng dần **và** refetch batch khi mount; hiện cả số `skipped`.

### 3. Trang quản trị `AdminSettings.jsx` (pattern modal `Users.jsx`, mở từ `AccountMenu.jsx`, chỉ `isAdmin`)
Tabs: **Tổ chức & chi nhánh** (form `name`/`branches`/`branding`; bớt branch còn tham chiếu → hộp cảnh báo "N tài khoản/M lô đang dùng", cần xác nhận) · **S3 nguồn** (bảng dense + form, "Test kết nối" chẩn đoán + cột last-checked; xóa còn tham chiếu → cảnh báo `force`) · **S3 đích** (tương tự) · **Audit log** (§Audit log) · **Audit truy cập** (access-log xem/tải gốc — §Vòng đời sau xử lý ④) · **Dead-letter/lỗi** (doc `dead`/`error` theo lô+loại, nút "retry hàng loạt" — §Quy mô cực lớn 6). Bảng theo §Chuẩn UI dense.

### 4. Bề mặt tiêu thụ (§Vòng đời sau xử lý)
- **Tìm kiếm** (`Search.jsx`): ô tra theo số phát hành/chi nhánh/thời gian, kết quả phân trang cursor (§Vòng đời sau xử lý ②).
- **Hàng chờ hậu kiểm** (`ReviewQueue.jsx`): "lấy việc" (claim), lọc theo chi nhánh/độ ngờ, form sửa + đánh dấu `reviewed`, counter tiến độ (§③). Khi mở doc đang bị người khác giữ lock → **read-only + "Đang được X hậu kiểm"**; lưu mà version lệch → báo "người khác vừa sửa, tải lại" (không mất sửa thầm lặng). Heartbeat giữ lock khi đang thao tác.
- **Export** (nút theo bộ lọc/lô): tạo job export nền → hiện link tải khi xong (§⑦).

### 5. `frontend/src/api.js` — theo pattern `handle(await fetch(...))`
`getBranding()`, `getSiteConfig()/updateSiteConfig()`, `getS3Connections()/createS3Connection()/updateS3Connection()/deleteS3Connection()/testS3Connection()`, `getBrowseSources()/browseMinio()/importFromMinio()`, `getAuditLog()`, `getAccessLog()`, `searchGcn()`, `claimReview()/submitReview()/getReviewQueue()`, `retryErrors()`, `createExport()/getExportStatus()`.

## Chuẩn UI dense — `MinioBrowser.jsx` + bảng admin mới

### Vì sao
Tham chiếu UI DataLens Agent ("Không gian tri thức", "Tệp của tôi"): breadcrumb ngắn, header đếm + hành động, ô lọc dưới header, list **1 dòng/tệp**, không viền/khoảng trắng dư. Theo mật độ này thay bảng `.et-grid` (`Users.jsx`) đang thoáng. **Không đổi màu brand** — tái dùng token teal/ivory (`--brand:#0f766e`, `--accent:#0d9488`, `--bg:#f6f4ea`, `--border:#e7e1d1`, `--radius-sm:7px` — `style.css:7-34`); chỉ đổi mật độ/spacing.

### Spec (`.tbl-dense`/`.file-row`, không đổi bảng cũ)
**Panel:** breadcrumb → header (badge số lượng nền `--accent-50` chữ `--brand` + tiêu đề + nút phải) → ô lọc full-width **client-side** (không gọi lại API mỗi keystroke) → list.
**Dòng (`.file-row`):** cao **34px** (dải 32-40px, cận dưới vì công cụ chuột nội bộ); padding ngang 10px, gap 8px. Trái→phải: checkbox 16px (file; ẩn folder trừ khi "chọn cả thư mục") → icon loại (folder amber `--amber`; file chip "PDF" nền `#fee2e2`/chữ `--err` như `.badge.error`) → tên (1 dòng `ellipsis`) → cột phải mờ `--text-3` (size + ngày). Hover `--accent-50` (`style.css:11`). Toàn dòng click.
**Trạng thái GCN đã import:** chấm `queued→--info`, `processing→--warn`, `done→--ok`, `error→--err` — **luôn kèm text/tooltip**, không dùng màu làm tín hiệu duy nhất.
**Empty/loading/error (bắt buộc):** trống → icon mờ + "Thư mục này trống"; tải → 5-6 skeleton 34px (không nhảy layout); lỗi kết nối → message cụ thể (từ `async_test_connection`) + "Thử lại"; **gốc external mất** → "File gốc không còn ở kho nguồn" (không màn trắng/500). **Phân trang folder:** `next_token` → nút "Tải thêm" cuối list.
**Bulk bar:** ≥1 chọn → thanh dính dưới header: "Đã chọn N tệp" + primary "Nhập vào lô" + ghost "Bỏ chọn".
**Icon:** dùng `Icon.jsx` (không emoji) — bổ sung `folder`, `file-text`, `chevron-right`, `search`, `upload` nếu thiếu.

### Phạm vi
`MinioBrowser.jsx` (full spec); tab S3 nguồn/đích (bảng dense 34px + cột chấm `last_check_status` 3 màu + text); tab Audit log (dense + accordion). **Không đổi** `ExtractTable.jsx`/`Users.jsx`.

## Giới hạn / ghi chú có chủ đích

- **Không mã hóa secret** (plaintext `secret_access_key` trong Mongo). API không trả secret qua HTTP, nhưng **`mongodump`/backup của deployment chứa secret trần** — bảo vệ backup tương đương bảo vệ khóa kho nguồn. KMS/encryption-at-rest ngoài phạm vi trừ khi yêu cầu.
- **Không health-check nền** — chỉ test on-demand, lưu `last-checked`; trạng thái "ok" có thể đã cũ; cron làm sau.
- **TLS:** mặc định `verify_tls=false` (nhất quán nội bộ). Nguồn qua **internet** nên bật verify (field đã có) — tắt trên internet là hở MITM.
- Mỗi tỉnh = deployment/DB riêng → **không sửa JWT/RBAC**; branch chỉ cần hợp lệ trong `site_config.branches` của deployment.

## Khảo sát: dùng datalens-agent làm trợ lý riêng cho AI-HUB

> **Khảo sát tương thích + đề xuất, KHÔNG phải scope cam kết đợt này.** Tách hẳn khỏi phần trên.

### 1. datalens-agent là gì
Harness LangGraph + deepagents domain-agnostic: lõi (agent loop, FastAPI, Postgres checkpoint/memory, Telegram) không biết nghiệp vụ; nghiệp vụ ở `harness/domains/<name>/` expose `DOMAIN = Domain(tools, skills_dir, instructions, setup)`, chọn qua `ACTIVE_DOMAIN`. Domain `ttcp`: tool đọc truy Mongo trực tiếp; tool ghi gắn `metadata={"hitl": True}` (Approve trước khi chạy). `harness/tenant.py` = 1 process phục vụ nhiều account (config theo `ContextVar`, login riêng, độc lập JWT AI-HUB).

### 2. Khớp và lệch
**Khớp (không sửa core):** domain plug-in (`harness/domains/ai_hub/`); HITL cho tool ghi khớp mutation-cần-audit; MinIO presign (`presign_ttcp_object`) đúng cho "tải gốc qua agent" (ký URL, không proxy byte); `AIHUB_API_KEY`/`X-API-Key` ([config.py:32]) — xác thực service-to-service sẵn có.
**Lệch, cần quyết định:**
- **Tenant:** mỗi deployment AI-HUB có **đúng 1 harness riêng** (thêm service vào compose tỉnh đó), bỏ lớp multi-account.
- **Auth:** **AI-HUB proxy** — user (JWT) gọi `POST /v1/assistant/chat`, AI-HUB verify rồi forward harness kèm `X-User-Id`+`X-API-Key`; không expose harness ra internet.
- **Ghi Mongo trực tiếp bỏ qua audit:** tool **ghi** gọi **REST AI-HUB** (qua `X-API-Key`), KHÔNG Mongo trực tiếp cho bảng có audit; chỉ tool **đọc** mới Mongo trực tiếp.
- **Actor thật:** thêm header `X-Acting-User` (chỉ tin khi đã xác thực bằng key) → `log_action(actor=f"agent:{acting_user}")`.
- **Hạ tầng:** harness cần Postgres riêng (AI-HUB chỉ Mongo/MinIO/Redis) — chi phí thật/deployment, cân nhắc quy mô 1 admin/tỉnh.
- **vLLM sẵn có** — dùng lại `VLLM_BASE_URL`, không thêm GPU.

### 3. Tool dự kiến `harness/domains/ai_hub/`
Đọc (không HITL, Mongo trực tiếp): `list_batches/get_batch`, `search_gcn/get_gcn`, `aggregate_gcn_stats(group_by=branch|status|day)` (mượn `aggregate_ttcp`), `get_site_config/list_s3_connections/get_audit_log`.
Ghi (HITL, REST qua `X-API-Key`+`X-Acting-User`): `browse_minio_source/import_from_minio`, `update_site_config/create|update|delete|test_s3_connection`.

### 4. Kết luận
Tương thích cao ở tầng pattern — không sửa lõi. Effort: (1) domain package mới (cỡ `ttcp/db.py`, phần ghi mỏng vì gọi REST); (2) `X-Acting-User` (nhỏ); (3) quyết định Postgres/tenant per-deployment. Không rủi ro kiến trúc lớn; **pha riêng sau khi tinh-config ổn định**.

## Smoke test (cổng nhanh sau mỗi deploy — chạy trước Verification đầy đủ)

Mục tiêu: trong ~5 phút xác nhận deployment **sống + không regress đường chính**. Fail bất kỳ bước nào → dừng, không cần chạy tiếp Verification.

1. **Boot sạch:** `docker compose up` → container `api`+`worker` lên, log không lỗi startup; `GET /health` (hoặc route sẵn có) 200.
2. **Seed tự chạy:** `GET /v1/settings/branding` trả đúng `org_name`/`copyright` của deployment (không rỗng, không 500) → `site_config` đã seed.
3. **Branch đọc từ DB:** `GET /v1/batches/branches` trả danh sách chi nhánh (khớp `site_config.branches`).
4. **Đường cũ còn sống:** đăng nhập → upload 1 PDF từ máy → batch tạo, doc chạy tới `done`, xem 1 trang được. (Chứng minh không phá luồng hiện tại.)
5. **Đường mới tối thiểu:** Admin thêm 1 S3 nguồn → "Test kết nối" ra `ok`; `CreateBatch` → "Từ kho MinIO" duyệt được 1 cấp thư mục; chọn **1 file** → import đồng bộ → Mongo có doc `gcn` `status=queued`, `source_connection_id` đúng, `s3_key` = key gốc, **không** object mới trong `AIHUB_BUCKET`.
6. **Worker nhặt được doc external:** doc ở bước 5 chuyển `queued→processing→done`, `page_count` điền đúng, xem trang gốc đọc đúng từ nguồn.

**Sau xử lý (sanity nhanh — 1 doc đã `done` là đủ):**

6b. **Phân quyền hiển thị:** đăng nhập lần lượt `admin`/`operator`/`viewer` → admin thấy "Cấu hình hệ thống", operator thấy import + hậu kiểm (không thấy tab S3/secret), viewer chỉ thấy tra cứu/xem; `operator` gọi `GET /v1/s3-connections` → 403.
7. **Tra cứu:** `GET /v1/gcn/search` theo số phát hành / chi nhánh của doc vừa `done` → trả đúng doc (cursor-based, không lỗi).
8. **Hậu kiểm:** mở hàng chờ review → "lấy việc" gán doc cho reviewer (lock); sửa + đánh dấu `reviewed` → counter `reviewed` tăng, không đè dữ liệu trích gốc.
9. **Audit truy cập:** `download` bản gốc 1 doc external → access-log ghi `{actor, gcn_id, action:download}`.
10. **Export:** `GET /v1/gcn/export?filter=<lô này>` → nhận file CSV/NDJSON đúng doc (streaming, không timeout).

Pass cả 10 → deployment ổn ở mức cơ bản (cả nạp lẫn tiêu thụ); sang Verification cho các nhánh sâu (import thư mục lớn, resume, dead-letter, dangling, guard, scale…).

## Verification

1. Restart Hà Nội → `site_config` tự seed, web y hệt (branch, branding, upload); audit index + partial unique index tạo thành công (build nền không khóa lâu); chạy **nhiều API worker** không tạo trùng doc `site` (upsert `$setOnInsert`).
2. **Chạy lại 2 script branch** (`backfill_branch` propagate; `delete_branch --branch "..."` dừng ở xác nhận) → validate async chạy, không "coroutine never awaited".
3. Admin → thêm S3 nguồn → "Test kết nối": thử **5 loại lỗi** (sai key/secret/bucket/endpoint-unreachable/AccessDenied) thấy message phân biệt; đúng → "ok"+`last_checked_at`. Test-draft khi **đang sửa** (không nhập lại secret) dùng secret đã lưu, không đòi nhập lại.
4. `CreateBatch` → "Từ kho MinIO" → chọn vài file → Mongo có `gcn` `status=queued`, `source_connection_id` đúng, `s3_key` = key gốc, KHÔNG object mới trong `AIHUB_BUCKET` cho các doc này.
5. **Idempotency:** chọn lại đúng thư mục/file lần 2 → `skipped>0`, tổng doc không tăng.
6. **Thư mục lớn (vài trăm file):** submit → response **ngay** (`importing`) + `import_jobs` doc queued; `file_count` tăng dần qua SSE, khớp tổng PDF; **reload trang giữa import** → UI refetch batch vẫn thấy số hiện tại; request không timeout.
6b. **Resume worker-queue:** **kill worker giữa import** (hoặc restart API) → sau `PROC_TTL` job `import_jobs` bị reclaim, import chạy tiếp tới đủ tổng PDF, **không nhân đôi** doc (skip-existing); batch không kẹt `importing`.
7. Worker `queued→processing→done`, `page_count` đúng; xem/tải external đọc đúng từ nguồn; cuts ở bucket đích.
8. **Dangling:** xóa/di chuyển file gốc bên nguồn sau khi queued → worker đưa doc `error` message rõ (không kẹt); xem/tải doc đó → "gốc không còn", không 500.
9. **File không phải PDF** (đổi đuôi `.pdf` cho 1 file rác) → worker `error` "không phải PDF" (magic-byte), không vỡ pipeline.
10. **Xóa connection còn tham chiếu** → `409`+số doc; `?force=true` mới xóa (cảnh báo UI).
11. **Bớt branch còn tham chiếu** trong Admin → cảnh báo "N tài khoản/M lô"; chỉ lưu khi xác nhận. Lưu `branches` rỗng → bị chặn 400.
12. Sửa `name`/branch/branding → reload login/tạo batch → UI cập nhật, không rebuild.
13. `GET /v1/audit-log` đủ 5 action; lọc `action`/`actor`; **grep response tìm secret đã nhập — KHÔNG xuất hiện** dòng nào.
14. **An toàn nguồn:** chạy `reset.py`/`delete_branch.py` → xác nhận **không** phát sinh request DELETE/PUT nào tới endpoint nguồn (chỉ đụng `AIHUB_BUCKET`); file gốc bên nguồn còn nguyên sau khi xóa 1 branch/lô external.
15. Luồng upload từ máy (cũ) → không regression.
16. (Nếu làm agent) hỏi "bao nhiêu hồ sơ queued" → tool đọc khớp Mongo; đổi 1 s3-connection → HITL approval → audit ghi `actor="agent:<username>"` đúng người.
17. UI dense: `MinioBrowser.jsx` + tab S3/Audit — dòng ~34px, hover đổi nền, ô lọc client-side, "Tải thêm" khi >1000 object, empty/loading/error đúng (cắt mạng nguồn để giả lỗi).
18. **Nhất quán cache đa-process:** chạy API **≥2 worker/process**; đổi secret 1 S3 nguồn (hoặc sửa branding) → trong ≤TTL (~30s) **mọi** process đọc file external bằng creds mới / trả branding mới (gọi lặp nhiều lần thấy hội tụ, không kẹt bản cũ ở process nào).
19. **Import đồng thời:** 2 user cùng import prefix chồng nhau vào **cùng lô** → tổng doc = hợp không trùng (skip-existing), `file_count` khớp số doc thực; không có doc nhân đôi.
20. **Cleanup streaming (không OOM):** tạo lô cắt ra **nhiều chục nghìn** cut object → chạy `delete_branch` → theo dõi RSS worker **không phình theo số object** (xóa theo trang), xóa hết, chạy lại resumable không lỗi.
21. **Counter mức lô:** so `batch.counts.{queued,processing,done,error}` với `count_documents` thực trên tập vừa phải → khớp tuyệt đối qua mọi chuyển trạng thái (kể cả khi kill worker giữa chừng rồi resume).
22. **SSE gộp:** lô lớn → xác nhận UI nhận **event mức lô định kỳ** (không phải hàng loạt per-doc); browser không đơ; đóng/mở lại tab vẫn thấy tiến độ đúng (refetch counter).
23. **Retry hàng loạt:** cố ý cho một mớ doc `error` (đổi đuôi rác + 1 nguồn timeout) → thao tác "retry tất cả error" đặt lại `queued` theo bộ lọc → chỉ doc lỗi tạm chạy lại tới `done`, doc "không phải PDF" giữ `error` (phân biệt `error_kind`).
24. **Nguồn đổi nội dung:** import 1 folder → doc `done`; thay nội dung 1 file gốc ở nguồn (giữ nguyên key, đổi etag) → import lại cùng folder → **chỉ file đó** re-queue + xử lý lại (etag mới), phần còn lại vẫn skip.
25. **Dead-letter:** đưa 1 "poison doc" (PDF làm render treo) → sau `attempts` vượt ngưỡng → doc thành `status="dead"`, **không** bị reclaim tiếp, pool vẫn xử lý doc khác bình thường.
26. **Tra cứu ở tập lớn:** với vài trăm nghìn doc, `GET /v1/gcn/search` theo field-có-index + cursor → trả nhanh (không quét toàn bộ), phân trang tới cuối không chậm dần (không dùng skip sâu).
27. **Hậu kiểm đồng thời cùng 1 file:** (a) 2 reviewer cùng "lấy việc" → không ai nhận trùng doc (claim atomic). (b) 2 reviewer **mở thẳng cùng 1 doc**: người thứ 2 thấy read-only + "Đang được X hậu kiểm"; (c) ép cả 2 cùng lưu (lock hết hạn) → người lưu sau nhận **409 "người khác vừa sửa"**, KHÔNG ghi đè mù (version-check); (d) đóng tab người đang giữ lock → sau TTL doc mở lại cho người khác.
28. **Audit truy cập:** `download`/xem gốc external → access-log ghi đủ `{actor, gcn_id, action, at}`; đối chiếu số bản ghi khớp số lần thao tác.
29. **Export streaming:** export lô lớn → job nền chạy, file ra bucket đích, link tải đúng dữ liệu; RSS không phình theo số doc (streaming, không load-all).
30. **Scoping chi nhánh:** `operator` thử import prefix chi nhánh khác → bị chặn; doc tạo ra mang đúng branch của operator, không gán nhầm.
31. **Tách quyền (3 role):** `operator` gọi `GET /v1/s3-connections` / `PATCH /settings/site` → **403** (chỉ admin); nhưng `operator` browse/import/**tạo lô-upload**/review/export được trong chi nhánh mình. `viewer` gọi tạo lô/import/export → **403**, chỉ tra cứu/xem/`download` (kèm access-log). `operator`/`viewer` **không đường nào** đọc được `secret_access_key` (kể cả khi dùng connection để import).
