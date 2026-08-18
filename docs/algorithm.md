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

## 7b. Nhiều LOẠI GIẤY trên cùng một pipeline — `doc_type`

Bốn loại đầu vào, khai ở registry [`app/doc_types.py`](../backend/app/doc_types.py):

| mã | tên trên giấy | chế độ gom |
|---|---|---|
| `gcn` | Giấy chứng nhận QSDĐ | `gcn` |
| `ddk` | Đơn đăng ký đất đai, tài sản gắn liền với đất (Mẫu 15 + 15a/15b/15c) | `mot_ho_so` |
| `kqdk` | Giấy xác nhận đăng ký đất đai (Chi nhánh VPĐK cấp) | `mot_ho_so` |
| `pcctt` | Phiếu thu thập thông tin đất đai | `mot_ho_so` |

Khung worker KHÔNG đổi — vẫn claim → render → detect → extract → ghi; chỉ tra
registry ở các điểm rẽ: `classify · extract · normalize · summarize · group_key ·
collect_keys · rows · cut_stem · detect_min_pages · dedup_trang`.

### Hai chế độ gom trang

`gcn` — một PDF chứa NHIỀU giấy: phân loại biên từng trang (cover/content/other) →
`groups_from_roles` suy nhóm tuyến tính → mỗi nhóm một lần extract. Không đổi.

`mot_ho_so` — một PDF là MỘT hồ sơ, các trang còn lại là tài liệu đính kèm (CCCD,
sơ đồ kỹ thuật, ảnh chuyển khoản, giấy viết tay cũ). Phân loại từng trang thành
`bieu_mau`/`dinh_kem`, VỨT phần đính kèm, đưa TOÀN BỘ trang biểu mẫu vào MỘT lần
extract.

> **Vì sao không gom tuyến tính**: khảo sát 90 file mẫu thật (2026-08-18) cho thấy
> thứ tự trang không đáng tin. `Đơn ĐK/1054768.pdf` xếp Mẫu 15 mặt trước (tr.1),
> 15a (tr.2), 15c (tr.3), CCCD (tr.4–5), rồi **Mẫu 15 mặt sau ở tr.6** — gom theo
> vị trí sẽ cắt mất đúng phần "Đề nghị cấp Giấy chứng nhận" và danh sách giấy tờ
> nộp kèm. Gom-tất-cả-trang-biểu-mẫu miễn nhiễm với thứ tự.

Fail-safe hai tầng: classify lỗi → giữ trang; không nhận ra trang biểu mẫu nào →
extract cả file (thà thừa còn hơn ghi `no_gcn` cho hồ sơ có dữ liệu thật).

### Loại trang trùng — [`app/anh_trung.py`](../backend/app/anh_trung.py)

Kho mẫu có file scan mỗi tờ HAI lần (bản màu + bản xám xoay ngang; `Kết quả
ĐK/583572.pdf` 58 trang ≈ 29 tờ). dHash 16×16 (256 bit), ngưỡng Hamming 18, giữ bản
đầu tiên. aHash 8×8 đã thử và LOẠI: hai tờ khác loại chỉ lệch 4/64 bit vì trang nào
cũng "trắng là chính" → loại nhầm trang thật.

Chỉ trả về INDEX bị loại; `images` giữ nguyên vị trí vì `page_indices` và khâu cắt
trang đều đánh chỉ số theo nó.

### Khóa nghiệp vụ

GCN dùng Số phát hành đọc từ giấy. Ba loại biểu mẫu dùng **khóa nguồn** —
`khoa_tu_nguon(doc)`: đường dẫn MinIO (doc import) hoặc tên tệp upload, bỏ đuôi
`.pdf`. Chốt cùng khách: ddk/pcctt không in số hiệu nào, dữ liệu lại gần như toàn
bộ là chữ viết tay, nên khóa suy từ nội dung sẽ sai; khóa nguồn thì chính xác tuyệt
đối và nối ngược được về hệ thống gốc.

### Cờ đi vào hệ thống ở đâu

```
POST /v1/batches            doc_type=<gcn|ddk|kqdk|pcctt>   (Form, mặc định gcn)
POST /v1/browse/{id}/import {"doc_type": "..."}             (JSON, mặc định gcn)
GET  /v1/gcn?doc_type=ddk                                   (lọc bảng)
```
Ghi lên **từng gcn doc** (không chỉ lên lô) → lô trộn nhiều loại vẫn xử lý đúng.
`import_jobs` mang `doc_type` xuống các doc nó sinh ra.

**Tương thích ngược**: doc/job cũ không có field → `doc_types.get(None)` trả GCN;
lọc `?doc_type=gcn` khớp cả doc thiếu field (`{"$in": ["gcn", None]}`).

**Chỉ GCN mới chạy** (gate `dt.hau_xu_ly_gcn`): vá SPH theo tên tệp, `gan_mdsdd`,
`chu_cuoi`, `_refresh_dup_group`, ghi `extracted_so_phat_hanhs`. Khóa biểu mẫu đi
đường riêng qua `extracted_keys` — nhét chung vào index Số phát hành là mời hai loại
giấy khác nhau "nghi trùng" nhau chỉ vì chung một dãy số.

**Cột bảng**: summary/rows của biểu mẫu tái dùng ĐÚNG tên khóa của GCN
(`so_phat_hanh`/`chu_su_dung`/`ngay_cap`/`to_ban_do`/`so_thua`) nên bảng trích xuất,
tìm kiếm và export CSV chạy được ngay; `khoa_chinh` + `doc_type` là phần bổ sung để
sau tách cột riêng mà không phải chạy lại kho.

⚠️ **Chưa chạy live** với vLLM thật — prompt của 3 loại mới soạn từ mẫu giấy, chưa
đo trên GPU. Dữ liệu cần bóc gần như toàn bộ là CHỮ VIẾT TAY nên tỷ lệ phải hậu kiểm
tay sẽ cao hơn GCN đáng kể.

### Smoke

```bash
# PURE (không cần hạ tầng)
docker compose exec api python -m app.doc_types      # registry: 42 KILL
docker compose exec api python -m app.anh_trung      # dedup trang: 12 KILL

# E2E qua API thật — đẩy PDF lên, chờ worker, soi kết quả
python3 backend/app/scripts/smoke_e2e_doc_types.py --tu-kiem        # tự kiểm, không cần server
docker compose exec api python -m app.scripts.smoke_e2e_doc_types \
    --api http://localhost:8000 -u admin -p '***' --loai pcctt tmp/mau --so-luong 5
```

E2E tách hai tầng kết luận: **KILL** = bất biến kỹ thuật vỡ (sai loại giấy, khóa
không bằng tên tệp nguồn, một file ra nhiều hồ sơ, thân JSON sai hình dạng, trang
trỏ ngoài phạm vi) → lỗi code, exit ≠ 0. **Độ điền** = tỉ lệ % từng trường bóc được
→ KHÔNG kill, vì ba loại này viết tay và ô trống có thể là dân bỏ trống thật; dùng
để so trước/sau mỗi lần sửa prompt.

---

## 8. Vận hành (đã có nút/endpoint)

- **Retry lỗi**: `POST /v1/gcn/retry-errors` — `error→queued`, mọi lô (admin) hoặc 1 lô,
  lọc `finished_at` (since/until). — [routes/gcn.py]
- **Giải phóng job kẹt**: `POST /v1/gcn/release-stuck` — `processing→queued` ngay (không chờ
  PROC_TTL), chỉ đụng doc cũ hơn `min_stale_seconds` để không giật doc worker vừa claim.
- Script CLI tương ứng: `app/scripts/retry_errors_all.py`, các script `requeue_*`, `backfill_*`.

Xem `test_eval.md` để biết cách chạy/kiểm.
