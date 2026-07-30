# Test & Eval — AI-HUB

> Ghi chú **cách smoke test + benchmark + eval**, tách rõ **máy serve** (production, có
> Mongo/MinIO/vLLM thật) và **máy cá nhân** (chỉ chứa code — KHÔNG chạy được luồng thật).
> Nguyên tắc house-style: **PURE** (thuần, không hạ tầng) chạy được mọi nơi; **E2E** cần hạ tầng.

---

## 0. Máy nào chạy được gì

| | Máy cá nhân (code) | Máy serve (docker) |
|---|---|---|
| Test PURE (`__main__` từng module) | ✅ nếu có Python deps | ✅ |
| Smoke E2E (Mongo thật) | ❌ (không tới được Mongo) | ✅ `docker compose exec …` |
| `bench_pipeline` (cần vLLM) | ⚠️ chỉ khi tới được `VLLM_BASE_URL` | ✅ |
| Kiểm dữ liệu Mongo | qua port-forward + pymongo (chỉ đọc) | ✅ |

Trên máy cá nhân, Mongo/MinIO/vLLM đều là dịch vụ nội bộ của serve → **mặc định không truy cập
được**. Có thể forward cổng Mongo để xem bằng Compass/pymongo (chỉ đọc/soi). Mọi thao tác ghi
hoặc chạy pipeline: **làm trên máy serve**.

---

## 1. Test PURE (thuần — không hạ tầng)

Mỗi module engine tự chứa smoke trong `__main__`, chạy độc lập. Ví dụ:
```bash
python -m src.extentions.multimodal.vlm_client      # 13 KILL: pool/failover/cooldown
python -m src.extentions.multimodal.chu_cuoi        # regex chủ cuối
python -m app.scripts.smoke --help                  # runner E2E theo stage (xem §2)
```
KILL đỏ ⇒ dừng, sửa GỐC (không vá vội). Đây là tầng chạy nhanh nhất, nên chạy trước khi push.

---

## 2. Smoke E2E (hạ tầng thật — trên máy serve)

Runner tập trung `app/scripts/smoke.py` (đọc-only, nối Mongo lỗi ⇒ stage tự SKIP không FAIL):
```bash
docker compose exec api python -m app.scripts.smoke chu_cuoi_real
docker compose exec api python -m app.scripts.smoke chu_cuoi_real --limit 50
```
Thêm khả năng ⇒ thêm 1 stage + đăng ký `_STAGES` (dọn trong `cleanup()` nếu stage có ghi).

### Thử pipeline trên 1 file (tune prompt/pipeline)
```bash
docker compose exec worker python -m app.scripts.try_pipeline /path/to.pdf
docker compose exec worker python -m app.scripts.try_pipeline a.pdf --detect-only --window 0
docker compose exec worker python -m app.scripts.try_pipeline a.pdf --thinking --save-images ./imgs --out out.json
```

---

## 3. Benchmark throughput (CÔNG CỤ CHÍNH cho tối ưu tốc độ)

`app/scripts/bench_pipeline.py` — đo "1 phút / 15 phút xử lý bao nhiêu bộ GCN" + **bóc tách thời
gian đi đâu** (render CPU vs detect VLM vs extract VLM) + **VLM utilization**. Gọi trực tiếp các
hàm leaf thật, tái tạo đúng orchestration của `run_job._pipeline` — không cần queue/S3, chỉ cần
tới được vLLM.

```bash
# baseline 60s, 3 file mẫu trong tmp/
docker compose exec worker python -m app.scripts.bench_pipeline tmp/*.pdf --duration 60

# 15 phút, thử nới trần VLM
docker compose exec worker python -m app.scripts.bench_pipeline tmp/*.pdf --duration 900 --vlm 16

# quét nhiều mức để tìm điểm bão hòa, ghi CSV
docker compose exec worker python -m app.scripts.bench_pipeline tmp/*.pdf --duration 120 \
    --sweep-vlm 4,8,16 --csv bench.csv
```

**Đọc kết quả** (chốt nút thắt — xem `overall_roadmap.md §5`):
- **VLM utilization = tổng-giây-call-VLM / (elapsed × MAX_VLM_CONCURRENT)**
  - ~1.0 → GPU là nút cổ chai → nới trần / thêm GPU / giảm token ảnh (PERF-2/6).
  - thấp → nghẽn nơi khác: render CPU, `MAX_IN_FLIGHT` thấp, **hoặc I/O MinIO (PERF-1)**.
- **vlm_wait** cao mà GPU rảnh → tăng `MAX_VLM_CONCURRENT`; cao mà GPU full → GPU là trần thật.
- breakdown render/detect/extract cho biết dồn công vào đâu.

> ⚠️ `bench_pipeline` **không** đo tầng MinIO (cố ý bỏ S3). Để đo riêng chi phí tải file, đo
> `storage.get_pdf` bằng một script phụ, hoặc so throughput bench (không S3) với throughput
> worker thật (có S3): chênh lệch lớn = chi phí MinIO (PERF-1).

### Quy trình eval một thay đổi hiệu năng
1. Ghi **baseline** (`--csv`) trước khi sửa.
2. Sửa (vd PERF-1/2/3).
3. Chạy lại cùng tham số, so files/phút + utilization.
4. Với thay đổi động tới ảnh (PERF-2 JPEG, PERF-6 DPI): **bắt buộc eval chất lượng** trên tập
   vàng (đừng chỉ nhìn tốc độ) — so dữ liệu bóc ra với nhãn đúng trước khi đổi mặc định.

---

## 3b. Quan sát luồng TRỰC TIẾP lúc đang chạy (`watch`)

`app/scripts/watch.py` — dashboard terminal, **chỉ đọc** (an toàn production). Đọc `batch.counts`
(rẻ, không quét gcn) → throughput hồ sơ/phút, số đang xử lý, ETA, lô đang chạy, lỗi.

```bash
docker compose exec api python -m app.scripts.watch                # mọi lô, refresh 5s
docker compose exec api python -m app.scripts.watch --interval 3
docker compose exec api python -m app.scripts.watch --batch <id>   # 1 lô
docker compose exec api python -m app.scripts.watch --timings      # + phân bố thời gian chặng
docker compose exec api python -m app.scripts.watch --once         # in 1 lần
```

**Bóc tách nút thắt bằng `--timings`**: cần bật đo chặng ở **worker** (không đổi API):
đặt `AIHUB_TRACE_TIMINGS=true` cho service worker rồi `docker compose up -d worker`. Mỗi hồ sơ
xong sẽ ghi `gcn.timings = {download, pipeline, cuts, page_count, n_groups}`. `watch --timings`
in p50/p90 mỗi chặng:
- **download** cao → nghẽn **MinIO** (PERF-1). So p50 trước/sau khi bật client pool để định lượng.
- **pipeline** cao → render CPU + VLM (dùng `bench_pipeline` bóc tách sâu hơn render vs detect vs extract).
- **cuts** cao → ghi S3 đích chậm (cân nhắc `AIHUB_BUILD_CUTS=false` hoặc PERF-1).

Tắt lại khi xong quan sát: bỏ `AIHUB_TRACE_TIMINGS` (mặc định tắt, zero overhead).

---

## 4. Kiểm/soi dữ liệu thật (máy serve)

```bash
# top lỗi theo error_kind + nội dung
docker compose exec -T api python - <<'PY'
import asyncio
from app.db import gcns
async def main():
    async for r in gcns().aggregate([
        {"$match": {"status": "error"}},
        {"$group": {"_id": "$error_kind", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}]):
        print(f"{r['n']:>8,}  {r['_id']}")
asyncio.run(main())
PY

# soi N doc mới xử lý nhất khớp điều kiện
docker compose exec worker python -m app.scripts.soi_gcn
```

### Thao tác vận hành thường dùng (đã có script/endpoint)
```bash
# retry mọi lô, lọc theo thời điểm lỗi + loại lỗi
docker compose exec api python -m app.scripts.retry_errors_all --error-kind transient --dry-run
docker compose exec api python -m app.scripts.retry_errors_all --error-kind transient

# giải phóng job kẹt processing (sau restart/build worker) — chỉ đụng doc cũ hơn 120s
docker compose exec -T api python - <<'PY'
import asyncio
from datetime import datetime, timezone, timedelta
from app.batch_counters import bump
from app.db import batches, gcns
async def main():
    stale = datetime.now(timezone.utc) - timedelta(seconds=120)
    base = {"status": "processing", "started_at": {"$lt": stale}}
    ids = [b for b in await gcns().distinct("batch_id", base) if b]
    tot = 0
    for bid in ids:
        r = await gcns().update_many({**base, "batch_id": bid}, {"$set": {"status": "queued"}})
        if r.modified_count:
            await bump(batches(), bid, processing=-r.modified_count, queued=r.modified_count); tot += r.modified_count
    print(f"giải phóng {tot:,} ({len(ids)} lô)")
asyncio.run(main())
PY
```

Xem `algorithm.md §8` + `features_issues.md` (OPS-1 NoSuchKey) cho ngữ cảnh.

---

## 5. Checklist trước khi deploy / nghiệm thu
- [ ] PURE smoke các module đụng tới đều xanh.
- [ ] Smoke E2E stage liên quan xanh (hoặc SKIP có lý do).
- [ ] Với thay đổi hiệu năng: có số bench trước/sau + (nếu đụng ảnh) eval chất lượng.
- [ ] Sau `docker compose up -d --build`: `docker compose ps` (3 service Up), mở UI cổng 3000,
      thử 1 lô nhỏ chạy queued→done, tab Lỗi hoạt động.
