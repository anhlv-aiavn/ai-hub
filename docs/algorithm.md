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
                                          không thư mục con: {Số GCN}_{tên thư mục gốc}_{tên file gốc}.pdf
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
2. **RESUMABLE**: nếu `item["qc"]` đã có verdict (từ 1 lần chạy trước — do lỗi ở bước SAU QC, hoặc
   do `/items/{id}/retry` giữ nguyên field này), BỎ QUA bước này — không gọi lại QC scanner, không
   bump lại `qc_stats_daily` (đã cộng ở lần thành công trước, cộng lại sẽ đếm trùng). Chỉ khi CHƯA
   có verdict mới gọi `qc_client.check_pdf(pdf_bytes)` — `POST /?format=json` tới
   `qc-scanner-server` (`QC_SCANNER_BASE_URL`). `503` là mã DUY NHẤT retry (bounded, theo
   `Retry-After`); `401`/`400` raise lỗi permanent, không retry (đúng hợp đồng API của service).
   Kết quả ghi vào `qc_items.qc` (verdict/reasons/metrics) VÀ bump `qc_stats_daily`.
   → Đúng yêu cầu thực tế: lỗi mạng/QC thì chạy lại được, nhưng KHÔNG bắt quét QC lại với file đã
   có verdict rồi (tránh tốn thêm 1 lượt gọi qc-scanner-server vô ích).
3. `verdict == "fail"` → `status=done` (KHÔNG phải lỗi hệ thống — đã xử lý xong, chỉ là không đạt).
4. `verdict` pass/warn → OCR: `run_job._pipeline(pdf_buf)` (hàm thuần, TÁI DÙNG, không sửa —
   xem quyết định [QC-1](features_issues.md#qc-decide-raw-ocr)) rồi `normalize_extractions(records)`
   (cùng bước chuẩn hoá đầu tiên pipeline GCN chính áp dụng, để dữ liệu nhất quán với
   `gcn.extractions`). `records` rỗng → `no_gcn`.
5. `records` có dữ liệu → lưu NGUYÊN VẸN vào `qc_items.ocr.records` (không chỉ đếm số lượng — cần
   tra cứu lại toàn bộ thông tin đã trích xuất: chủ sử dụng, thửa đất...), rồi
   `_classify_meta_of(mongo, config_id)` lấy TRƯỚC `ward_code` (= `qc_sync_configs.name`, mã
   Phường/Xã của kênh), rồi `run_job._build_cuts(gcn_id=item_id, batch_id=config_id, ...,
   dest_purpose="qc", naming_fn=_qc_cut_naming(item, ward_code or config_id))` — TÁI DÙNG nguyên hàm
   cắt/gộp trang GCN của pipeline chính, chỉ đổi `dest_purpose` để ghi vào MinIO đích RIÊNG
   (`storage._get_dest_client(purpose="qc")`), và đổi `naming_fn` để ghi vào THƯ MỤC RIÊNG THEO KÊNH
   (`{ward_code}/`, fallback `config_id` nếu kênh chưa có `ward_code`) — trong thư mục đó tên file
   PHẲNG `{Số GCN}_{tên thư mục gốc}_{tên file gốc}.pdf` ("Số GCN" = Số phát hành nếu đọc được (thiếu
   thì dùng id tạm), "tên thư mục gốc" = thư mục CHA trực tiếp của file trên kho nguồn — giữ lại làm 1
   phần tên để phân biệt nguồn gốc + giảm khả năng đè khi 2 thư mục khác nhau tình cờ trùng tên file).
   Đổi tên/vị trí lưu là QUYẾT ĐỊNH RIÊNG của QC Sync — `_build_cuts` mặc định (`naming_fn=None`,
   pipeline GCN chính) giữ nguyên khoá cũ `{batch_id}/{gcn_id}/cut-{ri}.pdf`, không đổi hành vi.
   **Rủi ro đã biết**: tách thư mục theo KÊNH đã loại bỏ rủi ro đè giữa 2 kênh khác nhau (trước đây
   ghi phẳng chung 1 đích) — vẫn còn rủi ro đè trong CÙNG 1 kênh nếu tên suy từ nội dung (Số GCN +
   thư mục gốc + tên file) trùng nhau giữa 2 lần quét (xem
   [QC-2](features_issues.md#qc-flat-naming-collision)).
6. **QC LẦN 2 (làm đẹp bản cắt)**: `_build_cuts` nhận thêm `correct_fn=qc_pipeline._qc2_correct` —
   gọi NGAY TRƯỚC KHI ghi mỗi bản cắt lên S3 đích. `_qc2_correct` gửi LẠI chính PDF vừa cắt (đã chỉ
   còn đúng trang của 1 GCN) qua `qc_client.check_pdf` lần 2, lấy field `image`/`pages[].image`
   (ảnh đã nắn phối cảnh + deskew, base64 PNG — cùng định dạng `qc-scanner-server` docs) và GHÉP LẠI
   thành PDF thay thế bản thô bằng `run_job._images_to_pdf`. Đây là bước RIÊNG, KHÔNG đụng tới QC
   lần 1 (vẫn chấm PDF gốc để quyết định OCR — xem quyết định
   [QC-1](features_issues.md#qc-decide-raw-ocr), không đổi) và KHÔNG dùng ảnh nắn cho OCR — chỉ áp
   dụng cho FILE XUẤT RA cuối cùng. Lỗi/không nắn được (mã lỗi QC, hoặc số ảnh trả về không khớp số
   trang gửi) → FALLBACK về bản cắt thô, verdict/lỗi QC-2 ghi vào `cuts[].qc2` để biết bản nào chưa
   nắn được — KHÔNG chặn pipeline, KHÔNG mất file.

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

**Quản lý lịch sử quét (debug/reset)**:
- `POST /v1/qc-sync/items/{id}/retry` — đưa 1 item về `queued`. GIỮ NGUYÊN field `qc` nếu đã có
  verdict (chỉ xóa `ocr`/`error`/timestamps) — `process_qc_item` (worker) tự nhận ra và bỏ qua gọi
  lại QC scanner (xem §9c bước 2, RESUMABLE). Chặn nếu đang `processing`, tránh đụng độ với lượt
  đang chạy dở.
- `DELETE /v1/qc-sync/items/{id}` — xóa 1 item khỏi lịch sử (gỡ chặn unique index → file được coi
  là "mới", quét lại ở lượt sau).
- `DELETE /v1/qc-sync/configs/{id}/items` — xóa TOÀN BỘ lịch sử quét (mọi file) của 1 kênh, để quét
  lại từ đầu cả thư mục. Cả 3 endpoint đều KHÔNG đụng file đã cắt đã ghi ở MinIO đích (chỉ xóa bản
  ghi theo dõi) và KHÔNG lùi `qc_stats_daily` đã cộng dồn khi xóa từng item lẻ (thống kê vận hành
  gần đúng, chấp nhận lệch nhỏ — xóa cả kênh thì xóa luôn `qc_stats_daily` của kênh đó, nhất quán).
  FE (`QcSync.jsx`) bắt buộc `window.confirm()` cảnh báo rõ hậu quả trước khi gọi.
- `GET /v1/qc-sync/items/{id}/source-pdf` — xem trực tiếp PDF NGUỒN (khác file đã cắt) qua tab mới,
  bấm vào cột "S3 key" trên bảng theo dõi; stream thẳng từ kho nguồn (`storage.get_pdf`), không lưu
  tạm ở server.
- `GET /v1/qc-sync/items/{id}/cuts/{cut_index}/pdf` — xem trực tiếp 1 file ĐÃ CẮT (khác PDF nguồn ở
  trên) qua tab mới, cột "File đã cắt" trên bảng theo dõi; đọc từ MinIO đích RIÊNG của QC Sync
  (`storage.get_pdf(key, dest_purpose="qc")` — `storage.get_pdf` mới thêm tham số `dest_purpose`,
  mặc định `"gcn"` nên không đổi hành vi các nơi gọi cũ).

**Tạm dừng/tiếp tục xử lý file đang chờ** (khác `enabled`/"Chạy ngay" — những cái đó điều khiển
việc QUÉT THÊM file mới, không phải xử lý backlog đã có): `PATCH /v1/qc-sync/configs/{id}` với
`{"items_paused": true|false}`. `worker.claim_qc_item` loại trừ item thuộc kênh có `items_paused`
qua 1 cache có throttle (`WORKER_QC_PAUSED_REFRESH_SECONDS`, mặc định 5s — không query
`qc_sync_configs` mỗi lần claim). File ĐANG xử lý dở khi bật tạm dừng vẫn chạy nốt (không bị ngắt
giữa chừng), chỉ file CHƯA claim mới bị chặn nhận. Tắt tạm dừng → worker tự nhặt lại backlog ở lượt
claim kế tiếp.

**Thống kê ở trang "Tổng quan"** (`ExportView.jsx` + component mới `QcSyncStats.jsx`, viewer trở
lên xem được, CHỈ XEM — khác trang quản trị "QC Sync" admin-only có thêm thao tác Chạy lại/Xóa/Dừng):

- **Khối "Tổng chất lượng QC"** (hero): % đạt (pass+warn / scanned) to, kèm thanh progress mảnh +
  số liệu quét/đạt/không đạt — heuristic "1 chỉ số quan trọng không cần vẽ chart" (skill `dataviz`).
  Dải KPI phụ bên dưới (`OcrKpiStrip`): đã cắt (item)/file đã cắt/không thấy GCN/không thấy file/lỗi.
- **3 biểu đồ SVG tự vẽ** (không dùng chart library, repo chỉ có react/vite) — QC (pass/warn/fail),
  OCR (ocr_done/no_gcn/no_file/error), số file đã cắt (`cuts_created` — KHÁC `ocr_done` là số item,
  1 item có thể ra >1 file cắt) — theo ngày/tuần/tháng, MỖI CỘT có nhãn tổng số trực tiếp phía trên
  (không chỉ dựa hover). Nhãn cột TUẦN là 1 khoảng ngày "10/08–16/08" (Thứ 2–Chủ nhật), KHÔNG phải
  1 ngày đơn — tránh hiểu lầm "hôm nay 11/08 sao cột lại ghi 10/08" (bug thực tế đã gặp: nhãn tuần
  trước đây chỉ hiện ngày Thứ 2 đầu tuần). "Tổng" hiện dạng khối hero + KPI, không vẽ chart cho 1
  giá trị. Nguồn dữ liệu: `GET /v1/qc-sync/stats/series?config_id=&days=` — chuỗi theo NGÀY từ
  `qc_stats_daily` (rollup có sẵn, gộp theo ngày bằng vòng lặp Python giống `get_stats`, KHÔNG
  aggregation pipeline riêng). Tuần/tháng = FE tự resample từ chuỗi ngày (cộng theo ISO week / tháng
  dương lịch, JS thuần).
- **Bảng "Theo Phường/Xã"** (`WardTable`, chỉ hiện khi đang xem "Tất cả kênh"): liệt kê MỌI kênh
  (kể cả kênh chưa hoạt động) kèm đã quét/đạt/không đạt/đã cắt/không GCN/lỗi trong khung đang chọn,
  sắp theo tên hoặc theo số đã quét. Bấm "Xem" mở ngay danh sách file gần đây của kênh đó
  (`WardItemsPanel`, tái dùng `GET /items`) — link xem PDF nguồn, link xem từng file đã cắt, nút
  "Xem OCR" mở modal JSON nội dung đã trích xuất (`qc_items.ocr.records`, đã lưu đầy đủ — xem mục
  trên). CHỈ XEM, không có Chạy lại/Xóa (những thao tác đó ở trang quản trị). Nguồn dữ liệu:
  `GET /v1/qc-sync/stats/by-config?range=` — đếm theo TỪNG kênh cho 1 khung (`day|week|month|all`,
  dùng chung hàm `_range_match` với `/stats`), 1 lần gọi thay vì N lần gọi `/stats?config_id=` phía
  FE cho từng kênh.
- Bộ lọc kênh trên toàn bộ khối dùng tên Phường/Xã làm nhãn thay cho `name` nội bộ; mỗi kênh QC
  Sync vốn đã ứng với đúng 1 prefix/thư mục nguồn (thường đặt `name` trùng mã P/X, vd "00004") nên
  lọc theo kênh ≈ lọc theo P/X, không cần gộp nhiều kênh. Tên P/X ưu tiên `qc_sync_configs.ward_name`
  nếu admin nhập tay (ghi đè), KHÔNG thì tự suy từ collection `qc_wards` (khớp `maXa` == `name`).

**Đồng bộ danh sách Phường/Xã** (`qc_wards`, panel "Danh sách Phường/Xã" trên trang admin
`QcSync.jsx`, thu gọn mặc định): `POST /v1/qc-sync/wards/sync` — body `{"url": "..."}`, URL admin tự
nhập MỖI LẦN đồng bộ trên UI (không hardcode trong hệ thống). Backend gọi `httpx.AsyncClient().get(url)`
— KHÔNG shell ra lệnh `curl` với chuỗi người dùng gõ (rủi ro command injection), kết quả tương đương
"chạy curl" nhưng an toàn, cùng idiom `qc_client.py`. Kỳ vọng response
`{"data": [{"id", "tenXa", "maXa"}, ...], "success": bool}`; `maXa` dùng làm `_id` (khoá tự nhiên,
tra theo mã xã O(1)). Mỗi lần đồng bộ GHI ĐÈ TOÀN BỘ danh sách cũ (`delete_many` rồi `insert_many`)
— FE `window.confirm()` cảnh báo trước nếu đang có dữ liệu (cùng pattern các thao tác ghi đè/xoá
khác trong trang, không có cờ `confirm` ở tầng API). `GET /v1/qc-sync/wards` liệt kê danh sách hiện
có; `_ward_map()` (helper nội bộ `routes/qc_sync.py`) build `{maXa: tenXa}` 1 lần/request, dùng ở
mọi nơi trả `ward_name` (`/configs`, `/stats/by-config`).
- 2 bump còn thiếu đã vá để đủ số liệu: `no_file` (trước đây nhánh `SourceObjectMissing` trong
  `process_qc_item` không bump gì cả) và `cuts_created` (số FILE cắt thật, cộng cùng lúc với
  `ocr_done` khi `_build_cuts` thành công).
- Index mới `qc_stats_daily().create_index("date")` — truy vấn theo khoảng ngày KHÔNG lọc
  `config_id` (biểu đồ/bảng "tất cả kênh") cần index riêng trên `date`, unique index
  `(config_id,date)` có sẵn không phục vụ được kiểu truy vấn này.

### 9e. Phase 2 — xem §10

"Phân loại" và "Làm mịn json đầu vào" (`qc_items.ocr.cuts[].classification`/`refined`) — đã code,
xem §10.

## 10. Phân loại hồ sơ + "làm mịn dữ liệu" (build đơn) — phase 2 QC Sync

> Mã: F-17 (`features_issues.md`). Code: `app/land_normalizer/` (thư viện port), `app/land_normalizer_adapter.py`
> (adapter), `app/worker/qc_pipeline.py::_classify_cuts`, `app/routes/qc_sync.py` (mục "Phân loại hồ
> sơ"), FE `QcClassification.jsx`. Port thuật toán chuẩn hoá + phân loại cấu trúc từ dự án nội bộ
> `vpdd-don-ai` (`/Users/minhdra/workspace/aia/vpdk/vpdd-don-ai`) — **CHỈ port phần cấu trúc**,
> KHÔNG port "phân loại nội dung biến động" bằng Claude API (yêu cầu bỏ hoàn toàn, không có
> dependency/API key nào cho bước này trong ai-hub).

```
qc_items.ocr.records[ri]["result"]["Đăng ký"][0]     (entry OCR — 1 record = 1 cut, xem cuts[ri])
        │
        ▼  raw_record_from_cut(entry, cut, ward_code, item_id)   (app/land_normalizer_adapter.py)
RawRecord (ai_gcn_*/ai_chu_su_dung/ai_thua_dat/ai_tai_san/ai_bien_dong ← gán thẳng từ entry;
           ai_chu_cuoi ← chu_cuoi_for_entry(entry) đã có sẵn; parcels_json ← [{"ma_xa": ward_code}])
        │
        ▼  build_payload([raw], registry)          (app/land_normalizer/pipeline.py — PORT nguyên)
Payload (PascalCase, khớp payload.json của HSQ)  ──────────────► cuts[i].refined
        │
        ▼  classify_structural(payload)     (app/land_normalizer/classification/structural.py)
PhanLoaiCauTruc (SoChuSoHuu/SoThua/CoDaMucDich/CoSuDungChungVaRieng/NhanCauTruc) ─► cuts[i].classification
```

**Vì sao port được gần nguyên vẹn**: schema JSON OCR của ai-hub (`Đăng ký` →
`Giấy chứng nhận`/`Chủ sử dụng`/`Thửa đất`/`Thông tin nhà ở`/`Biến động`, xem
`src/extentions/multimodal/prompt.py`) khớp gần 1:1 với các field alias Vietnamese-key mà
`RawRecord` (input contract của `vpdd-don-ai`) đã định nghĩa sẵn — dự án gốc thiết kế `RawRecord`
tách biệt khỏi nguồn dữ liệu (Excel/API HSQ) chính vì lý do này (ADR-001 của họ). ai-hub cũng đã có
sẵn `chu_cuoi_for_entry()` (`src/extentions/multimodal/chu_cuoi.py`, dùng chung với backfill
`chu_cuoi` của pipeline GCN chính) — hàm PURE suy "chủ cuối" từ lịch sử biến động, khớp đúng field
`ai_chu_cuoi` cần cho resolver "Xác định chủ sử dụng" (Vợ chồng/Cá nhân/Hộ gia đình).

**`ward_code`** = `qc_sync_configs.name` (kênh QC Sync đặt tên trùng mã Phường/Xã theo quy ước đã
có ở §9) — dùng thay cho `parcels_json[0].ma_xa` (dữ liệu "thu thập thực địa" mà dự án gốc có
nhưng ai-hub không có) để resolver `DonDangKy.XaId` vẫn hoạt động: adapter tự tạo
`parcels_json=[{"ma_xa": ward_code}]` giả lập.

**`dest_bucket`** = `s3_connections.bucket` của `qc_sync_configs.dest_connection_id` (tra thêm 1
lần/item) — điền `HoSoQuet.BucketName` (bucket MinIO ĐÍCH thật chứa file đã cắt). Sửa 2026-08-11:
resolver gốc `ho_so_quet.py` fallback về 1 tên bucket CỐ ĐỊNH (`"hni-kh515-new"`, bucket HSQ của
khách hàng khác trong dự án gốc) khi thiếu `pdf_path_bucket_name` — SAI hoàn toàn với ai-hub nếu
không truyền `dest_bucket`. Đã bỏ hằng số này, để trống khi thiếu thay vì hiện 1 bucket sai.

**"Loại giấy"** (`GiayChungNhans[0].GiayChungNhan.TenLoaiGiayChungNhan`, suy từ định dạng
`SoHieuGiayChungNhan` + ngày cấp — 6 quy tắc NĐ60/NĐ90/NĐ88/NĐ43/Luật 1993/2003, xem
`resolvers/giay_chung_nhan.py`, KHÔNG sửa gì khi port) đã chạy tự động cùng lúc với phần cấu
trúc — chỉ chưa hiện ở bảng "Phân loại" ban đầu, đã bổ sung cột riêng (`GET /classifications`
project thêm `loai_giay` qua `$arrayElemAt`, tránh kéo cả `refined` về FE).

**Tính TỰ ĐỘNG, MIỄN PHÍ** (không gọi API ngoài nào — thuần Python): `_classify_cuts()` chạy ngay
trong `process_qc_item` sau `_build_cuts()` thành công, với MỖI cut — bọc `try/except` riêng từng
cut (1 cut lỗi không chặn cut khác, cùng tinh thần "1 GCN lỗi không chặn cả batch" của dự án gốc).
Ghi trực tiếp vào `cuts[i].refined`/`cuts[i].classification` (KHÔNG phải field top-level
`qc_items.classification`/`refined` như ghi chú "để chỗ sẵn" ban đầu ở §9e cũ — vì 1 file nguồn có
thể chứa NHIỀU GCN/cut, mỗi cut cần kết quả riêng).

**Không merge "attempts" nhiều lần OCR trùng GCN** (khác ADR-002 của dự án gốc) — mỗi cut tự đứng
thành 1 `RawRecord` độc lập (`build_payload([raw], registry)` chỉ 1 attempt). Nếu cùng 1 GCN được
quét lại ở 1 `qc_item` khác (2 lần sync khác nhau), sẽ ra 2 kết quả `refined` độc lập thay vì gộp
lại lấy giá trị tốt nhất — giới hạn v1, xem Issue mở trong `features_issues.md`.

**Endpoint**:
- `POST /v1/qc-sync/items/{item_id}/cuts/{cut_index}/reclassify` — chạy lại build_payload +
  classify_structural cho 1 cut (KHÔNG chạy lại QC/OCR, chỉ đọc lại `item.ocr.records` đã có sẵn).
  Dùng khi thuật toán đổi, hoặc lần tính tự động ban đầu lỗi.
- `GET /v1/qc-sync/classifications?config_id=&q=&structural_label=&page=` — bảng "Phân loại"
  (1 dòng/1 cut), aggregation `$unwind` trên `qc_items` (collection nhỏ, không cần tránh aggregate
  như `gcns()` 600k dòng).
- `GET /v1/qc-sync/items/{item_id}/cuts/{cut_index}/refined-payload?download=` — trả JSON
  `cuts[i].refined` — đây là dữ liệu SẼ LÀ input cho API "kiểm tra đơn" (`create-registration`) của
  HSQ nếu người dùng tự đem đi dùng; **ai-hub KHÔNG gọi API đó** (không có `item_id`/token của hệ
  thống HSQ) — chỉ hiển thị để copy/tải JSON thủ công.

FE (`QcClassification.jsx`, tab "Phân loại" ngay sau "QC Sync"): bảng lọc theo kênh/Số phát
hành/nhãn cấu trúc, mỗi dòng có nút "Xem JSON" (modal, nút Copy + Tải file) và "Phân loại lại".
KHÔNG có ô nhập Bearer token, KHÔNG có "Kiểm tra đơn"/"So sánh với HSQ" — các phần này gắn với API
HSQ thật, ngoài phạm vi ai-hub.
