# Thuật toán luồng xử lý AI-HUB

> Mô tả **thuật toán** của các luồng chính (ingest → trích xuất → gom nhóm → hậu kiểm).
> Nguồn sự thật là code; tài liệu này giải thích *vì sao* và *cách* các bước ghép lại.
> File tham chiếu ghi kèm dạng `path:line` để tra ngược.

## 0. Bức tranh tổng thể

```
                 ┌─────────────┐        ┌──────────────┐
Upload / Import → │  gcn (Mongo)│  poll  │   Worker     │  VLM (vLLM pool)
   (S3 nguồn)     │ status=queued├───────►│ streaming    ├──────► detect + extract
                 └─────────────┘  claim  │ pool async   │◄────── JSON
                        ▲               └──────┬───────┘
                        │ counters             │ ghi
                 ┌──────┴──────┐        ┌───────▼──────┐
                 │  batch      │◄───────│ extractions  │
                 │  .counts    │ bump   │ + cuts (S3)  │
                 └─────────────┘        └──────────────┘
```

Mongo vừa là **kho dữ liệu** vừa là **hàng đợi** (Mongo-as-queue). Không có RQ/Celery:
worker poll doc `status=queued`, claim nguyên tử, xử lý, ghi kết quả. Tiến độ đọc từ
`batch.counts` (counter duy trì tăng dần) — **không** `count_documents` ở quy mô chục triệu.

---

## 1. Ingest — đưa hồ sơ vào hàng đợi

Hai đường vào, cùng đích: tạo doc `gcn{status:"queued"}` + `bump(batch.counts, queued+1)`.

### 1a. Upload trực tiếp (`POST /v1/batches`) — [routes/batches.py:66-119]
```
với mỗi file PDF:
    data = await file.read()
    s3_key = "{batch_id}/{gcn_id}.pdf"
    storage.put_pdf(s3_key, data)           # ghi S3 ĐÍCH
    pages = storage.page_count(data)        # đếm trang tại chỗ (pdfium)
    gcns.insert_one({... status:"queued"})
init_counts / bump(queued = n)              # counter lô
```

### 1b. Import thư mục kho nguồn (`import_jobs`) — [worker/import_job.py]
Cho kho MinIO nguồn có sẵn hàng trăm nghìn file. **Không** tải file về lúc import — chỉ
liệt kê key và tạo doc trỏ tới nguồn (`source_connection_id`), đọc read-only khi xử lý:
```
claim import_job (queued | processing treo)
lặp STREAM theo trang (list_objects_v2, max_keys=500):
    insert_many(ordered=False) mỗi chunk        # bỏ qua trùng key êm
    bump(batch.counts, queued += len(chunk))
    heartbeat: cập nhật started_at + list_token  # kẹt giữa chừng → tự chạy tiếp
```
Bất biến: **không bao giờ ghi/xóa trên kho nguồn** (chỉ đọc).

---

## 2. Worker — vòng lặp claim (streaming pool) — [worker/main.py]

```
mỗi vòng poll (POLL_INTERVAL≈2s):
    _sweep_dead()                            # processing treo & vượt MAX_ATTEMPTS → dead
    trong khi len(in_flight) < MAX_IN_FLIGHT:
        bid = _next_batch_id()               # round-robin fairness giữa các lô
        doc = _claim(bid) hoặc _claim(None)  # claim có/không giới hạn lô
        nếu không có doc: break
        in_flight.add(task(process_doc(doc)))
    claim import_jobs / export_jobs (trần riêng)
    gom task xong, nghỉ tới vòng sau
```

### 2a. Claim nguyên tử — [worker/main.py:63-90]
```
find_one_and_update(
    { $or: [ {status:queued [, batch_id]},
             {status:processing, started_at < now-PROC_TTL} ] },   # reclaim treo
    { $set:{status:processing, started_at:now}, $inc:{attempts:1} },
    return BEFORE)
```
- **queued**: việc mới. **processing treo**: worker cũ chết giữa chừng (reclaim sau PROC_TTL=30′).
- `attempts++` mỗi lần claim → nền tảng dead-letter.

### 2b. Fairness round-robin — [worker/main.py:47-61]
`distinct(batch_id, {status:queued})` cache mỗi RR_REFRESH_INTERVAL(3s), xoay vòng để 1
lô khổng lồ không làm đói các lô nộp sau.

### 2c. Dead-letter (poison) — [worker/main.py:93-110]
`processing` treo **và** `attempts ≥ MAX_ATTEMPTS(3)` → `status=dead, error_kind=poison`.
Ngừng reclaim (doc làm worker treo cứng — không phải exception bắt được) để không bào mòn pool.

---

## 3. `process_doc` — xử lý 1 hồ sơ (1 PDF) — [worker/run_job.py:251]

```
1. tải PDF: storage.get_pdf(s3_key, source_connection_id)   # ĐÍCH nếu None, NGUỒN nếu có id
   kiểm magic-byte %PDF → không phải → ValueError (error_kind=permanent)
2. records, images = _pipeline(pdf_buf)                     # xem §4
3. normalize_extractions(records)
4. suy trạng thái cuối:
     skip_reason  → "skip"          (vd too_many_pages)
     có error     → "error"         (error_kind=transient)
     records rỗng → "no_gcn"        (KHÔNG phải lỗi — không có bìa GCN)
     ngược lại    → "done"
5. nếu done & BUILD_CUTS: _build_cuts()  → ghi cut-*.pdf lên S3 ĐÍCH
6. hậu xử lý THUẦN (không thêm call model): gan_mdsdd, chu_cuoi (regex), summary, group_key
7. update_one(gcn, {...})
8. nếu done & có Số phát hành: _refresh_dup_group (đánh dấu nghi trùng nội dung)
9. bump(batch.counts, processing-1, <status>+1) → _rollup(batch.status, SSE)
```

Lỗi bất kỳ ở bước 1–2 → nhánh `except`: ghi `status=error` + `error_kind` + `finished_at`,
bump `error+1`, bắn SSE lỗi (không mất toast). Doc **không bao giờ kẹt** `processing`.

---

## 4. `_pipeline` — render → detect → extract — [worker/run_job.py:115]

```
n = count_pages(pdf)                          # ProcessPool (pdfium không thread-safe)
nếu n == 0: return [],[]
nếu n > MAX_PAGES(250): return skip("too_many_pages")   # an toàn RAM/thời gian
images = pdf_to_corrected_images(pdf, dpi=200, max=2000) # render + xoay orientation (ONNX)
groups = _detect_groups(images)               # xem §4a
records = gather( _run(g) for g in groups )   # extract song song mỗi nhóm
```

### 4a. Detect — phân loại biên từng trang — [run_job.py:91, detect_gcn]
```
nếu số trang ≤ DETECT_MIN_PAGES(5): coi cả file = 1 giấy (KHỎI gọi VLM)   # tiết kiệm GPU
ngược lại:
    roles = gather( classify_page(img_i) )    # mỗi trang 1 call VLM: cover/content/other
    groups = groups_from_roles(roles)         # suy nhóm TUYẾN TÍNH (quyết định cục bộ)
```
Vì sao từng-trang thay vì cửa sổ: mỗi call 1 ảnh → chính xác cao, batch tốt, **không** lỗi
mốc cửa sổ / nhồi nhiều ảnh / rớt trang. `classify_page` lỗi → fail-safe "content" (giữ trang).

### 4b. Extract mỗi nhóm — [run_job.py:136]
```
async _run(group):
    imgs = [images[i] for i in group]
    async with _vlm_sem():                    # trần fan-out toàn cục (bound RAM base64)
        result = await wait_for(extract(imgs), timeout=EXTRACT_TIMEOUT=3000s)
```

### 4c. Điều phối concurrency
- `_VLM_SEM` = `Semaphore(total_vlm_concurrency())` = `PER_ENDPOINT × số_endpoint` — bao **mọi**
  call detect+extract, chặn fan-out để bound RAM (mỗi call chờ giữ ảnh base64 vài MB).
- Trong `vlm_client.chat_json`: chọn endpoint **ít việc nhất** (`_pick_least`), semaphore
  **riêng mỗi máy** (`PER_ENDPOINT_CONCURRENCY`), **failover** + **circuit-breaker** (máy vừa
  lỗi bị cooldown `ENDPOINT_COOLDOWN`s, né chọn lại) — [vlm_client.py:103-213].

---

## 5. Gom nhóm & khóa nghiệp vụ = **Số phát hành**

- `group_key_of(records)` = Số phát hành chính → gom tờ bổ sung về GCN gốc.
- `extracted_so_phat_hanhs[]` = mọi Số phát hành trong hồ sơ (index `extracted_so_phat_hanhs`).
- **Nghi trùng nội dung** `_refresh_dup_group` — [run_job.py:204]:
  ```
  others = gcn.find({extracted_so_phat_hanhs ∈ sph_list, _id ≠ self})
  set self.{dup_suspect, dup_candidates=others}
  với mỗi other: tính LẠI TOÀN BỘ danh sách của họ (đối xứng, tự chữa lành dữ liệu lệch cũ)
  ```
  Đối xứng + ghi đè (không `$addToSet`) để 2 hồ sơ trùng nhau luôn ra danh sách nhất quán.
  ⚠️ Chi phí ghi: xem `features_issues.md#perf-dup-group`.

---

## 6. Hậu xử lý suy diễn (thuần, không GPU)

Chạy ngay trong pipeline vì thuần chuỗi/regex — không thêm call model vào hot path GPU-bound:
- **MĐSD** (`gan_mdsdd`) — gắn Mã mục đích sử dụng đất vào từng mục đích (`mdsdd_version`).
- **Chủ cuối** (`chu_cuoi`, REGEX-only) — suy chủ hiện tại từ chuỗi Biến động; ca mập mờ đánh
  `canh_bao` để **backfill LLM định kỳ** quét sau (không chặn ingest) — [run_job.py:232].

---

## 7. Trạng thái & counter

`batch.counts` = `{queued, processing, done, error, no_gcn, skip, dead}` — [batch_counters.py].
Mọi điểm chuyển trạng thái gọi `bump()` cạnh update Mongo. `_rollup` suy `batch.status`
(`done` khi `queued+processing==0`) + publish SSE (throttle cho lô lớn, ngưỡng
`SSE_AGG_FILE_THRESHOLD=500`).

Vòng đời 1 doc:
```
queued → processing → done | no_gcn | skip | error
                          error  ──(retry)──► queued
   processing (treo) ──(reclaim/release)──► queued
   processing (poison) ─────────────────► dead
```

---

## 8. Vận hành (đã có nút/endpoint)

- **Retry lỗi**: `POST /v1/gcn/retry-errors` — `error→queued`, mọi lô (admin) hoặc 1 lô,
  lọc `finished_at` (since/until). — [routes/gcn.py]
- **Giải phóng job kẹt**: `POST /v1/gcn/release-stuck` — `processing→queued` ngay (không chờ
  PROC_TTL), chỉ đụng doc cũ hơn `min_stale_seconds` để không giật doc worker vừa claim.
- Script CLI tương ứng: `app/scripts/retry_errors_all.py`, các script `requeue_*`, `backfill_*`.

Xem `test_eval.md` để biết cách chạy/kiểm.

---

## 9. QC Sync — kiểm chất lượng + OCR + cắt GCN (pipeline mới, song song)

> Mã: F-16 (`features_issues.md`). Code: `app/qc_client.py`, `app/worker/qc_pipeline.py`,
> `app/routes/qc_sync.py`, FE `QcSync.jsx`. KHÔNG đụng pipeline GCN chính (§1-8) — chỉ TÁI DÙNG
> `_pipeline`/`_build_cuts` cho bước OCR/crop.

```
                Đồng bộ (liệt kê stream, dedup)          QC (qc-scanner-server ngoài)
MinIO nguồn ──────────────────────────────► qc_items ──────────────────────► qc_stats_daily
(cấu hình qua UI)   qc_sync_job (mirror         │  status=queued              (Mongo, mỗi
                    import_job.py)              │                             ngày/tuần/tổng)
                                                 ▼
                                    qc_item (worker claim)
                                                 │
                                    tải PDF gốc từ MinIO nguồn
                                                 │
                                    QC (qc_client.check_pdf) ──► verdict fail → status=done (KHÔNG lỗi)
                                                 │ pass/warn
                                                 ▼
                              OCR = run_job._pipeline(pdf_buf)  [TÁI DÙNG, PDF gốc — không dùng
                                                 │                ảnh đã nắn QC trả về, xem QC-1]
                                    records rỗng → status=no_gcn
                                                 │ records
                                    run_job._build_cuts(..., dest_purpose="qc")
                                                 │
                                          MinIO đích RIÊNG (purpose="qc") — PHẲNG,
                                          không thư mục con: {tên GCN}_{tên file gốc}_cropped.pdf
```

### 9a. Cấu hình (UI → `qc_sync_configs`)

Người dùng chọn: 1 **S3 nguồn** có sẵn (`s3_connections role=source`, dùng chung danh sách với
pipeline GCN) + 1 **S3 đích riêng** (`s3_connections role=destination purpose="qc"` — KHÔNG
singleton, khác đích `purpose="gcn"` của pipeline chính) + prefix + chu kỳ quét lại
(`interval_seconds`, mặc định `QC_SYNC_DEFAULT_INTERVAL_SECONDS=300`) + bật/tắt.

### 9b. `qc_sync_job` — discovery (mirror `import_job.py`)

`maybe_schedule_qc_sync_jobs()` (chạy mỗi vòng poll worker, tự throttle) tạo 1 job mới cho mỗi
config `enabled` đã tới hạn (`last_run_at` quá `interval_seconds`) và chưa có job nào đang chạy.
Job liệt kê **STREAM** prefix nguồn (`list_objects_v2` phân trang, không nạp cả kho vào RAM),
`insert_many(ordered=False)` mỗi trang vào `qc_items` — trùng `(source_connection_id, s3_key)` bị
**unique index từ chối êm** (đây là TOÀN BỘ cơ chế "không chạy lại file đã QC", không đọc trước khi
ghi). Mỗi lượt là **full re-scan** prefix (S3 liệt kê theo thứ tự key, không theo mtime — không
resume "chỉ lấy file mới" được giữa các chu kỳ); heartbeat + `list_token` mỗi trang cho khả năng
tự phục hồi nếu worker restart giữa chừng.

### 9c. `qc_item` — QC + OCR + crop 1 file

Claim atomic (queued hoặc processing-treo quá `PROC_TTL`, giống nhánh `gcn`). `process_qc_item`:

1. `storage.get_pdf(s3_key, source_connection_id)` — lỗi phân loại giống pipeline GCN
   (`SourceObjectMissing`→`no_file`, khác→`error` transient).
2. `qc_client.check_pdf(pdf_bytes)` — `POST /?format=json` tới `qc-scanner-server`
   (`QC_SCANNER_BASE_URL`). `503` là mã DUY NHẤT retry (bounded, theo `Retry-After`); `401`/`400`
   raise lỗi permanent, không retry (đúng hợp đồng API của service). Kết quả ghi vào
   `qc_items.qc` (verdict/reasons/metrics) VÀ bump `qc_stats_daily` (atomic `$inc`, upsert theo
   ngày UTC — không `count_documents` trên `qc_items` khi đọc thống kê).
3. `verdict == "fail"` → `status=done` (KHÔNG phải lỗi hệ thống — đã xử lý xong, chỉ là không đạt).
4. `verdict` pass/warn → OCR: `run_job._pipeline(pdf_buf)` (hàm thuần, TÁI DÙNG, không sửa —
   xem quyết định [QC-1](features_issues.md#qc-decide-raw-ocr)). `records` rỗng → `no_gcn`.
5. `records` có dữ liệu → `run_job._build_cuts(gcn_id=item_id, batch_id=config_id, ...,
   dest_purpose="qc", naming_fn=_qc_cut_naming(item))` — TÁI DÙNG nguyên hàm cắt/gộp trang GCN của
   pipeline chính, chỉ đổi `dest_purpose` để ghi vào MinIO đích RIÊNG
   (`storage._get_dest_client(purpose="qc")`), và đổi `naming_fn` để KHÔNG tạo thư mục con — ghi
   PHẲNG ngay tại bucket đích với tên `{tên GCN}_{tên file gốc}_cropped.pdf` ("tên GCN" = Số phát
   hành nếu đọc được, thiếu thì dùng id tạm). Đổi tên/vị trí lưu là QUYẾT ĐỊNH RIÊNG của QC Sync —
   `_build_cuts` mặc định (`naming_fn=None`, pipeline GCN chính) giữ nguyên khoá cũ
   `{batch_id}/{gcn_id}/cut-{ri}.pdf`, không đổi hành vi.
   **Rủi ro đã biết**: đặt phẳng + tên suy từ nội dung (không theo batch/id) → 2 kênh đồng bộ khác
   nhau ghi CÙNG 1 đích và tình cờ ra cùng tên (trùng Số phát hành + trùng tên file gốc) sẽ ĐÈ lên
   nhau. Chấp nhận cho v1 theo đúng yêu cầu; nếu cần an toàn hơn, cân nhắc thêm hậu tố phân biệt.

### 9d. Vận hành

Đọc thống kê: `GET /v1/qc-sync/stats?range=day|week|month|all` (aggregate `qc_stats_daily`, tập
bounded theo số ngày). Theo dõi item: `GET /v1/qc-sync/items` (phân trang, lọc theo `status`/
`qc.verdict` — FE có dropdown lọc + hiện `error`/`error_kind` chi tiết cho item lỗi/không thấy
file, phục vụ debug "chạy đến đâu, lỗi ở đâu"). Chạy quét ngay (bỏ qua chờ `interval_seconds`):
`POST /v1/qc-sync/configs/{id}/run` — nếu đã có lượt `queued`/`processing`, trả 409 kèm
`detail.job` (thông tin lượt đang chạy) thay vì chỉ báo lỗi suông.

**Theo dõi lượt đang chạy + dừng giữa chừng**:
- `GET /v1/qc-sync/configs/{id}/active-job` — lượt `qc_sync_job` đang chạy của 1 kênh (hoặc
  `null`), gồm `scanned`/`enqueued`/`skipped` để FE hiện tiến độ trực tiếp trên bảng cấu hình
  (`QcSync.jsx` component `ActiveJobRun`, poll mỗi 3s trong lúc có lượt đang chạy).
- `POST /v1/qc-sync/jobs/{id}/cancel` — hủy 1 lượt. `queued` (chưa worker nào claim) → hủy NGAY
  tại chỗ, không cần cờ. `processing` → cắm `cancel_requested=true` trên job doc; worker
  (`process_qc_sync_job`) đọc lại cờ này NGAY TRONG heartbeat mỗi trang liệt kê (1 round-trip,
  dùng `find_one_and_update` trả về doc sau update) — dừng ở checkpoint kế tiếp, không thể ngắt
  ngang 1 call S3 đang chạy dở. Job dừng theo cách này có `status="cancelled"` (khác `"done"`/
  `"error"`), KHÔNG bị coi là lượt đang chạy nên `run_now`/`maybe_schedule_qc_sync_jobs` cho phép
  tạo lượt mới ngay.

**Lưu ý vận hành**: bước OCR dùng CHUNG pool vLLM với pipeline GCN sản xuất — xem
[QC-1](features_issues.md#qc-shared-vlm).

### 9e. Chưa làm (phase 2 — theo đúng yêu cầu)

"Phân loại" và "Làm mịn json đầu vào" — `qc_items.classification`/`refined` để sẵn field rỗng
trong schema, chưa code logic.
