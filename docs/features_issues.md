# Features & Issues — AI-HUB

> Sổ đăng ký **tính năng + vấn đề** của dự án. Mỗi mục có: mã, mức ưu tiên, trạng thái,
> mô tả, bằng chứng (`path:line`), và hướng xử lý. Cập nhật khi raise/đóng.
>
> Ưu tiên: **P0** chặn/đắt nghiêm trọng · **P1** đáng làm sớm · **P2** cải thiện · **P3** nice-to-have.
> Trạng thái: 🔴 mở · 🟡 đang làm · 🟢 xong · ⚪ backlog.

---

## A. ISSUES — Hiệu năng

### 🔴 NÚT THẮT HIỆN TẠI: VLM extract (GPU decode) {#bottleneck-vlm}

Đo thực tế 2026-07-30 (`watch --timings`, sample 219 doc, hồ sơ p50 **2 trang**):

| Chặng | p50 | p90 | Ghi chú |
|---|---|---|---|
| download (MinIO) | 1.57s | 7.20s | ~1% — đã tối ưu (PERF-1) |
| render (CPU) | 1.88s | 5.99s | ~1.5% |
| detect (VLM) | ~0s | ~0s | file ≤5 trang bỏ qua classify |
| **extract (VLM)** | **117.7s** | **171s** | **~98% thời gian** |

**Vì sao**: log vLLM cho generation ~1000 tok/s *tổng* với ~128 request đồng thời (`Running`≈128)
→ mỗi request ~6–8 tok/s. **GPU bão hòa decode.**

**Đã đo bằng `probe_vlm` (2026-07-30, chạy cùng 128 request khác)**:
- output chỉ **461 completion_tokens** (đã gọn — KHÔNG có gì để cắt), prompt 2704 tok, **75.6s**.
- `implicit_off ≡ explicit_off`, `reasoning=0` → **THINKING ĐÃ TẮT THẬT, reasoning KHÔNG xảy ra**
  → nghi phạm "reasoning ngầm" (mục 1 cũ) **ĐÃ LOẠI**. 75s = 461 tok ÷ ~6 tok/s = decode bị chia
  cho 128 luồng. Đây là **giới hạn năng lực decode GPU thuần túy**.

**Throughput ≈ 1000 tok/s ÷ (token đầu ra / hồ sơ)** — không tăng bằng cách nới thêm concurrency
(đã bão hòa; nới chỉ tăng latency/hồ sơ). Đòn bẩy thật:
1. **Thêm GPU / endpoint vLLM** — throughput tăng tuyến tính (chắc chắn nhất).
2. **Thử nới `max_num_seqs` của vLLM** — KV cache mới ~46–52% (còn dư RAM) → có thể còn headroom
   decode; xác nhận bằng `bench_pipeline --sweep-vlm` (nếu tok/s tổng tăng → đáng nới, kèm tăng
   `MAX_VLM_CONCURRENT` cho khớp).
3. **Giảm token đầu ra/hồ sơ** — đòn bẩy trực tiếp NHƯNG đụng prompt/schema vendor và **rủi ro
   chất lượng** → đã chốt ưu tiên chất lượng, GÁC lại (xem [#decide-png] tinh thần tương tự).

App-side (PERF-1/3/4) đã/không còn giúp cho phần này — đây là bài toán **năng lực GPU**.

**Đòn bẩy từ config serve vLLM thực tế** (2 máy H100, mỗi máy 1 GPU, `vllm 0.22.1`,
Gemma-4-26B-A4B-NVFP4), ưu tiên nghi ngờ:

1. ~~reasoning ngầm~~ **ĐÃ LOẠI** bằng `probe_vlm` (reasoning=0, implicit≡explicit). Bỏ qua.
2. **1 GPU/máy** (`device_ids: ["0"]`). Nếu box H100 có nhiều GPU → đang phí. `nvidia-smi` kiểm;
   có thì `--tensor-parallel-size N` (giảm latency) hoặc thêm replica (tăng throughput).
   Đây là đòn bẩy chắc chắn nhất (throughput tuyến tính theo số GPU).
3. **`--max-num-seqs 128`** khớp `Running≈128`; KV cache mới ~50% → còn RAM. **Thí nghiệm rẻ, làm
   trước**: nới 1 máy lên 256, restart, nhìn log `Avg generation throughput`. Tăng >1000 →
   memory-bound còn headroom → giữ + tăng `MAX_VLM_CONCURRENT` (64→128/endpoint). Vẫn ~1000 →
   compute-bound → chỉ thêm GPU mới giúp. (`probe_vlm` khi worker DỪNG cho latency đơn-luồng để so.)
4. **`--max-model-len 100000`** quá thừa (seq thật ~5k token: ảnh ~560 soft-tok×2 + output). Hạ
   xuống ~16384 có thể cho lịch/nhiều seq tốt hơn. Rủi ro thấp, thử + đo.
5. **tecotec THIẾU `--enable-prefix-caching`** (vpdkhn có). System prompt lặp lại giống hệt mọi
   call → prefix cache giúp prefill. Thêm cho nhất quán (vLLM mới có thể đã default-on — xác nhận).
6. `--mm-processor-kwargs max_soft_tokens=560` — ảnh đã gọn, KHÔNG phải vấn đề (khớp với render
   nhanh 1.88s). Giữ nguyên.
7. **Các cờ "cho đủ feature" là TRƠ nếu không dùng**: `--enable-auto-tool-choice` +
   `--tool-call-parser` chỉ hoạt động khi request gửi `tools` (extract không gửi);
   `--reasoning-parser` chỉ tách output. Không ảnh hưởng tốc độ — cứ để.

---

### A′. ISSUES — Hiệu năng tầng ứng dụng (đã/đang xử lý)

### ⚡ PERF-1 · P0 · 🟢 · MinIO tạo connection MỚI mỗi request (không pool) {#perf-minio-pool}

> **XONG.** Client pool sống lâu (bọc lớp app, không đụng vendor), kill-switch `AIHUB_S3_POOL=false`.
> Code: `app/s3_util.py` (`pooled_s3/pooled_get/pooled_put`), `app/storage.py` (`_s3_get/_s3_put`).
>
> **Kết quả đo thực tế** (`watch --timings`, 2026-07-30): download p50 **1.57s** / p90 7.20s —
> chỉ ~1% thời gian mỗi hồ sơ. **MinIO KHÔNG còn là nút thắt.** Toàn bộ thời gian giờ nằm ở
> **extract (VLM)**: p50 **117.7s** / p90 171s (render 1.88s, detect ~0). Nút thắt thật đã dời
> sang **GPU decode của vLLM** — xem [Nút thắt hiện tại](#bottleneck-vlm) bên dưới.


**Triệu chứng**: throughput ingest thấp hơn nhiều so với năng lực GPU; 502 hàng loạt khi ghi
cut lên đích (đã phải thêm cờ tắt `AIHUB_BUILD_CUTS`).

**Gốc rễ**: `MinioClient.async_get_object` / `async_put_object` **tạo `aioboto3.Session()` +
`session.client()` mới trong MỖI lần gọi** — mỗi call là một bắt tay TCP + TLS mới tới endpoint
HTTPS. — [backend/src/extentions/minio_helper.py:71-89, 236-250]

Lớp cache ở `storage.py` (`_get_dest_client`/`_get_source_client`, TTL 30s) chỉ cache **wrapper
giữ credential**, KHÔNG phải client boto3 → **không tái dùng connection nào** trên hot path.
— [backend/app/storage.py:49-73]

Mỗi hồ sơ trong pipeline mở connection mới ít nhất: **1× tải** (`get_pdf`) + **N× ghi cut**
(`_build_cuts` → `put_pdf`, mỗi cut 1 connection) — [worker/run_job.py:180-201]. Ở 1M+ hồ sơ,
chi phí handshake cộng dồn lấn át cả thời gian GPU → GPU đói việc trong khi worker chờ TLS.

**Bằng chứng phụ**: path *listing* đã gặp đúng bệnh này và được vá bằng `open_s3_client` (mở 1
client dùng chung cho cả chuỗi trang) — [backend/app/s3_util.py:26-40]; nhưng path **ingest chưa
được migrate**. Cờ `AIHUB_BUILD_CUTS=false` ra đời để né 502 khi ghi — chính là triệu chứng của
issue này.

**Hướng xử lý** (ưu tiên cao nhất — đây là câu hỏi "tối ưu MinIO→trích xuất" của dự án):
1. **Tái dùng client boto3 sống lâu** theo endpoint, cache trong worker/process (không mở/đóng
   mỗi call). aioboto3 client là async context manager → giữ mở qua một "client manager" cấp
   worker, hoặc dùng 1 `aiobotocore` client pool. Kỳ vọng: bỏ handshake/call → giảm p50 tải file
   rõ rệt, GPU no việc hơn.
2. Chỉnh `botocore.Config(max_pool_connections=...)` đủ lớn (mặc định 10) khớp `MAX_IN_FLIGHT`.
3. Cân nhắc **HTTP** (không TLS) nếu MinIO ở mạng nội bộ tin cậy — bỏ hẳn chi phí TLS.
4. Đo bằng `bench_pipeline` trước/sau (xem `test_eval.md`) để định lượng.

**Ghi chú**: sửa ở `minio_helper.py` là "vendor" — theo house-style ưu tiên bọc ở lớp app
(`storage.py`/`s3_util.py`) thay vì sửa vendor. Có thể thêm hàm `get_pdf`/`put_pdf` dùng
`open_s3_client` (đã có) thay cho `MinioClient.async_*`.

---

### 🔒 Quyết định: GIỮ ẢNH PNG gửi VLM (không đổi JPEG) {#decide-png}

**Đã chốt — KHÔNG đề xuất lại.** Ảnh gửi model phải là **PNG** (không nén mất mát). GCN là tài
liệu pháp lý; nén JPEG làm nhiễu nét chữ/dấu/số nhỏ → **rủi ro sai dữ liệu bóc ra**, không đánh
đổi lấy tốc độ. Đầu vào đúng như hiện tại thì chất lượng mới đảm bảo. Tối ưu tốc độ tìm ở nơi
khác (PERF-1 MinIO, PERF-3/4 render), **không đụng định dạng ảnh**.

---

### ⚡ PERF-3 · P1 · 🔴 · Temp file + `os.fsync()` mỗi PDF khi render {#perf-fsync}

`pdf_to_corrected_images` ghi mỗi PDF ra `NamedTemporaryFile` kèm **`os.fsync()`** (ép ghi xuống
đĩa vật lý) rồi ProcessPool đọc lại — [backend/src/extentions/multimodal/make.py:112-116]. `fsync`
đắt và không cần cho tính đúng (temp cục bộ, đọc lại ngay trong cùng máy). Ở throughput cao, fsync
tuần tự là điểm nghẽn I/O ẩn.

**Hướng**: bỏ `os.fsync` (giữ `flush`); hoặc truyền thẳng `bytes` cho subprocess (pdfium mở được
từ bytes) thay vì qua file — giảm cả fsync lẫn round-trip đĩa.

---

### ⚡ PERF-4 · P1 · 🔴 · Hai lần submit ProcessPool + serialize base64 qua process {#perf-procpool}

Mỗi PDF submit ProcessPool **2 lần**: `count_pdf_pages_from_bytes` (đếm trang) rồi render —
[make.py:46-56, 118-137]. Ngoài ra kết quả render là **base64 PNG string** truyền ngược qua ranh
giới process (pickle copy) — payload lớn bị copy giữa process.

**Hướng**: gộp đếm-trang vào trong job render (mở pdfium 1 lần). Cân nhắc trả **bytes PNG** thay
vì chuỗi base64 để giảm kích thước pickle (giữ nguyên PNG — xem [Quyết định giữ PNG](#decide-png));
hoặc render trong thread (pdfium tuần tự hóa bằng 1-thread executor như `storage._RENDER_POOL` đã
làm cho preview) nếu process overhead > lợi.

---

### ⚡ PERF-5 · P2 · 🔴 · `_refresh_dup_group` ghi khuếch đại O(n²) trên hot path {#perf-dup-group}

Mỗi hồ sơ `done` có Số phát hành: query mọi doc cùng SPH rồi **ghi lại từng doc đó**, và **lặp
qua từng candidate** chạy lại toàn bộ refresh — [worker/run_job.py:204-227, 366-381]. Trong 1 cụm
trùng lớn, đây là O(n²) lượt ghi Mongo trên hot path ingest.

**Hướng**: (a) chỉ refresh khi tập SPH thật sự đổi; (b) dời sang backfill định kỳ / hàng đợi phụ
thay vì đồng bộ trong `process_doc`; (c) chặn số candidate xử lý mỗi lần. Cân nhắc trade-off với
tính "tự chữa lành" mà thiết kế hiện tại cố ý có.

---

### ⚡ PERF-6 · P2 · ⚪ · Tinh chỉnh DPI/kích thước ảnh & thinking theo tập vàng {#perf-dpi}

`AIHUB_RENDER_DPI=200`, `RENDER_MAX_SIZE=2000`, `ENABLE_THINKING` — mỗi thông số đổi trực tiếp số
token ảnh + thời gian sinh của VLM (nút cổ chai GPU). Chưa có bằng chứng 200 DPI / 2000px là điểm
tối ưu chất lượng-vs-tốc-độ.

**Hướng**: quét DPI (150/175/200) và max-size trên **tập vàng** đo cả độ chính xác lẫn
throughput; tắt thinking nếu không cải thiện chính xác. Dùng `bench_pipeline --sweep-vlm` +
eval chất lượng.

---

### ⚡ PERF-7 · P2 · 🔴 · Thiếu index ghép cho claim & fairness {#perf-index}

Query claim dùng `{status, batch_id}` và `{status, started_at}`; fairness dùng
`distinct(batch_id, {status:queued})` — [worker/main.py:52,76]. Nhưng index hiện chỉ có **đơn
trường** `status`, `batch_id`, `started_at` rời — [backend/app/db.py:54-82]. `distinct` theo
`status` rồi bới `batch_id` không được phủ; claim reclaim quét theo `status` rồi lọc `started_at`.

**Hướng**: thêm compound `{status:1, batch_id:1}` (phủ được distinct fairness + nhánh claim
queued) và `{status:1, started_at:1}` (nhánh reclaim + `_sweep_dead`). Kiểm bằng `explain()` ở
quy mô thật.

---

### ⚡ PERF-8 · P2 · ⚪ · `page_count` đồng bộ trong request upload {#perf-pagecount-upload}

`POST /v1/batches` gọi `storage.page_count(data)` (pdfium, đồng bộ, in-process) cho **từng** file
ngay trong request handler — [routes/batches.py:79-82]. Upload lô lớn → request chậm + block event
loop API. Worker dù sao cũng render lại (biết số trang).

**Hướng**: bỏ đếm ở upload (để `page_count=0`, worker điền), hoặc đưa vào executor.

---

### ⚡ PERF-10 · P1 · 🟡 · Audit luồng share (claim/reclaim/sweep) đa worker {#perf-share}

Audit khi chạy nhiều worker (quan sát `processing`=384 = 3 worker × MAX_IN_FLIGHT 128):

- ✅ **Claim KHÔNG trùng lặp**: `find_one_and_update` nguyên tử — 2 worker không cùng giật 1 doc.
- ⚠️ **Rủi ro trùng khi reclaim**: doc `processing` sống > `PROC_TTL`(1800s) bị worker khác reclaim
  → xử lý 2 lần + `processing` trừ 2 lần (drift âm). Hiện avg latency ~5′ nên chưa xảy ra, nhưng
  **oversubscribe đẩy latency lên** → tăng rủi ro. Giữ `PROC_TTL` > p99 latency; giảm oversubscribe.
- 🟢 **`_sweep_dead` chạy mỗi vòng poll (≤2s, có khi sub-giây) → ĐÃ throttle** `WORKER_SWEEP_INTERVAL`
  (mặc định 30s). Giảm mạnh query Mongo nền khi nhiều worker. — [worker/main.py]
- 🔴 **Oversubscribe (chưa tối ưu)**: tổng client concurrency (vd 3 worker × 128 = 384) **vượt tổng
  slot server vLLM** (`max-num-seqs`×số máy, vd 128×2=256) → ~128 request nằm chờ (Waiting) vô ích,
  giữ RAM ảnh + phồng latency, KHÔNG thêm throughput. **Nên: Σ(MAX_VLM_CONCURRENT×worker) ≈ Σ slot
  server.** Đây là config/ops (không phải bug code).
- 🟡 Fairness `distinct(batch_id,{status:queued})` mỗi 3s/worker trên corpus lớn khi thực tế chỉ
  1 lô chạy — tốn (thiếu compound index PERF-7). Cân nhắc nới `WORKER_FAIRNESS_REFRESH_SECONDS`.

### ⚡ QC-1 · P1 · 🔴 · `qc_item` (OCR) dùng CHUNG pool vLLM với pipeline GCN sản xuất {#qc-shared-vlm}

Bước OCR của pipeline **QC Sync** (F-16) tái dùng nguyên `run_job._pipeline` — nghĩa là cùng
`_VLM_SEM`/pool vLLM đang là [nút thắt #1](#bottleneck-vlm) của pipeline GCN sản xuất. Bật QC Sync
với `WORKER_QC_ITEM_MAX_CONCURRENT` cao có thể làm chậm pipeline chính.

**Hướng xử lý hiện tại (v1)**: mặc định `WORKER_QC_ITEM_MAX_CONCURRENT=2` (thấp), chỉ nới khi xác
nhận đủ dư GPU cho cả 2 pipeline (đo bằng `bench_pipeline`/`watch --timings` khi cả 2 cùng chạy).
Chưa có ưu tiên/isolation giữa 2 pipeline ở tầng vLLM — nếu cần, cân nhắc endpoint vLLM riêng cho
QC Sync hoặc hàng đợi có ưu tiên (P2, chưa làm).

### 🔒 Quyết định: OCR của QC Sync dùng PDF GỐC, không dùng ảnh đã nắn QC trả về {#qc-decide-raw-ocr}

**Đã chốt cho v1.** `qc-scanner-server` trả về ảnh đã nắn thẳng/cắt biên (`?format=json` có field
`image`/`pages[].image`) — về lý thuyết dùng ảnh này cho OCR có thể chính xác hơn ảnh scan gốc.
V1 KHÔNG dùng: OCR gọi `run_job._pipeline(pdf_buf)` với PDF gốc tải từ MinIO nguồn, y hệt pipeline
GCN chính — rủi ro thấp nhất (không phải re-eval chất lượng OCR trên input mới), tái dùng nguyên
vẹn hàm đã kiểm chứng. Nâng cấp sau nếu cần (ghép ảnh QC trả về thành pdf_buf giả rồi feed vào
cùng pipeline) — chưa làm, chưa đo tác động chất lượng.

## B. ISSUES — Đúng đắn / vận hành

### OPS-1 · P1 · 🟢 · NoSuchKey → trạng thái `no_file` (tách khỏi "Lỗi"/retry) {#nosuchkey}

> **ĐÃ XỬ LÝ.** `storage.get_pdf` raise `SourceObjectMissing` (con của `SourceObjectUnavailable`)
> cho `NoSuchKey/404/NoSuchBucket`; `run_job` phân loại sang **`status="no_file"`**
> (`error_kind="missing_source"`) — RỜI ô "Lỗi", KHÔNG bị `retry-errors`/`release-stuck` quét
> (chúng chỉ nhắm `error`/`processing`). Thêm state `no_file` vào `batch_counters`, legend UI
> ("Không có tệp"). Dữ liệu cũ: `app/scripts/reclass_no_file.py` (dry-run trước).
>
> Cân nhắc còn lại: NoSuchKey đôi khi do lệch prefix (không phải mất thật) — nếu nghi, xác minh
> vài key trên MinIO trước khi coi là mất hẳn.

### OPS-4 · P1 · 🟢 · Counter drift (processing âm) do release-stuck giật doc đang chạy {#counter-drift}

> **ĐÃ XỬ LÝ.** Extract 1 hồ sơ mất ~117s p50 / 171s p90 — gần/ vượt ngưỡng `min_stale_seconds`
> mặc định cũ **120s** của `release-stuck` → nút "Giải phóng job kẹt" giật cả doc đang extract dở
> → doc xử lý 2 lần, `bump(processing=-1)` chạy 2 lần → `batch.counts.processing` **âm**.
> Sửa: nâng mặc định `min_stale_seconds` **120→600s** (> p90; worker vẫn tự reclaim doc CHẾT sau
> `PROC_TTL`=1800s). Dọn counter đã lệch: `app/scripts/reconcile_counts.py` (tính lại từ status thật).
> Bài học: ngưỡng release phải > thời gian xử lý thật của 1 hồ sơ.

### OPS-2 · P2 · ⚪ · `dead` (poison) cần công cụ soi thủ công {#dead-tool}

Doc `dead` nằm ngoài phạm vi retry hàng loạt (đúng thiết kế) nhưng chưa có UI/CLI để soi vì sao
poison. **Hướng**: thêm trang/endpoint liệt kê `dead` + lý do + link file để người vận hành quyết.

### OPS-3 · P3 · ⚪ · Xử lý trùng nội dung mới ở mức "cảnh báo" {#dup-policy}

`dup_suspect` chỉ cảnh báo, không chặn/gộp. Chính sách gộp bản trùng (giữ bản chất lượng cao hơn)
chưa có. **Hướng**: định nghĩa chính sách cùng khách hàng (xem `need_exchange.md`).

---

## C. FEATURES — Đã có (đã ship)

| Mã | Tính năng | Ghi chú |
|----|-----------|---------|
| F-01 | Upload lô PDF + import thư mục kho nguồn (stream) | `routes/batches.py`, `worker/import_job.py` |
| F-02 | Trích xuất VLM: detect biên từng trang + extract song song | `worker/run_job.py`, `multimodal/*` |
| F-03 | Pool nhiều máy vLLM: cân tải ít-việc-nhất + failover + circuit-breaker | `vlm_client.py` |
| F-04 | Gom tờ bổ sung theo **Số phát hành** + đánh dấu nghi trùng | `summary.py`, `_refresh_dup_group` |
| F-05 | Suy MĐSD + chủ cuối (regex, trong pipeline) + backfill LLM ca mập mờ | `mdsdd.py`, `chu_cuoi*.py` |
| F-06 | Bảng trích xuất + đối soát PDF↔GCN + hậu kiểm (overrides/lock/version) | `routes/gcn.py`, FE `Reconcile.jsx` |
| F-07 | Đặt lại tên + tải bộ (zip PDF+JSON) + xuất CSV / xuất nền | `routes/gcn.py`, `export_job.py` |
| F-08 | Cắt file per-GCN lên S3 đích (cờ `AIHUB_BUILD_CUTS`) | `_build_cuts` |
| F-09 | Tiến độ realtime SSE (throttle/gộp cho lô lớn) | `bus.py`, `_rollup` |
| F-10 | Counter lô duy trì (không count_documents) | `batch_counters.py` |
| F-11 | Mongo-as-queue: claim nguyên tử, reclaim treo, dead-letter | `worker/main.py` |
| F-12 | **Retry lỗi**: mọi lô/1 lô + lọc thời gian `finished_at` | `POST /v1/gcn/retry-errors` |
| F-13 | **Giải phóng job kẹt** processing→queued (ngưỡng an toàn) | `POST /v1/gcn/release-stuck` |
| F-14 | Quản lý S3 nguồn/đích, phân quyền lô, audit log, JWT/1-phiên | `routes/{s3_connections,users,audit,auth}.py` |
| F-15 | Dry-run trích xuất (bucket/collection riêng, TTL tự dọn) | `routes/dryrun.py` |
| F-16 | **QC Sync**: đồng bộ MinIO nguồn → chấm chất lượng qua qc-scanner-server ngoài → OCR (tái dùng VLM) → cắt GCN → MinIO đích riêng | `worker/qc_pipeline.py`, `qc_client.py`, `routes/qc_sync.py`, FE `QcSync.jsx` |

## D. FEATURES — Đề xuất (backlog)

| Mã | Tính năng | Ưu tiên | Ghi chú |
|----|-----------|---------|---------|
| N-01 | Client MinIO pool sống lâu (nền của PERF-1) | P0 | Mở khóa throughput |
| N-02 | Trang "Dead-letter" soi poison + hành động | P1 | OPS-2 |
| N-03 | Dashboard hiệu năng: files/phút, VLM utilization, breakdown thời gian | P1 | Bề mặt hóa `bench_pipeline` |
| N-04 | Cây lịch sử giấy (chuỗi Biến động/Số phát hành theo thời gian) | P2 | Pha sau trong PLAN.md |
| N-05 | Chính sách gộp bản trùng nội dung | P2 | OPS-3 + need_exchange |
| N-06 | Retry-từng-bước (chỉ chạy lại extract, giữ ảnh đã render) | P2 | Tiết kiệm render lại |

---

## Cách dùng file này
- Raise issue mới: thêm mục vào §A/§B với mã tăng dần, priority, `path:line` bằng chứng.
- Đóng issue: đổi 🔴→🟢, ghi commit/PR đã sửa.
- Mục hiệu năng nên kèm **số đo trước/sau** từ `bench_pipeline` (xem `test_eval.md`).
