# Chủ cuối · Mục đích sử dụng chuẩn hóa · Rà soát cắt GCN
> sobagi plan — phạm vi: 600k giấy đã có trong AI-HUB + các lần chạy sau

## Quyết định thiết kế trọng yếu

1. **Phase 1 & 2 (MĐSD + Chủ cuối) là SUY DẪN THUẦN từ `extractions` đã nằm trong Mongo** — không render lại, không gọi VLM ảnh, không đụng GPU. 600k xử lý offline được trong khi pipeline vẫn chạy bình thường. §Nguyên tắc lõi.
2. **`DETECT_MIN_PAGES = 5` là QUY TẮC NGHIỆP VỤ ĐÚNG, không phải lỗi.** Bộ GCN ≤5 trang = 1 giấy — chốt, không đụng vào. Toàn bộ công sức rà cắt dồn vào nhánh **>5 trang**. §Hiện trạng cắt.
3. **Đo thiệt hại cắt KHÔNG cần GPU.** `page_indices` + `page_count` + Số phát hành đã nằm sẵn trong `extractions` → tính được "trang bị rớt" và "nhóm gộp nhầm" trên **cả 600k, miễn phí, phủ 100%**. GPU chỉ dùng ở bước xác nhận nguyên nhân trên mẫu nhỏ. §Phase 0.1a.
4. **Mỗi phase qua 2 TẦNG SMOKE mới coi là xong** — PURE (logic thuần, stdlib) + E2E (Mongo thật). "Xanh PURE, chưa E2E" = **chưa xong**. Theo house-style `datalens-agent/docs/ecosystem/testing-and-eval.md`: runner stage tập trung, `assert …, "KILL [n] …"`, chạy TRONG CONTAINER. §Smoke test.
5. **Trường dẫn xuất đặt SONG SONG, không nhét vào `extractions` raw** — cùng bất biến "giữ raw" của `review.overrides` ([PLAN.md §Data model]). Raw = sự thật VLM; dẫn xuất = tính lại được bất cứ lúc nào. §Schema.
6. **`algo_version` trên mỗi trường dẫn xuất** → đổi rule chỉ backfill lại doc lệch version, không quét mù 600k. §Backfill.
7. **Backfill BẮT BUỘC streaming + bulk_write.** Pattern hiện có `backfill_chu_su_dung_norm.py:23` dùng `to_list(length=None)` — **OOM ở 600k**, không được sao chép. §Backfill.
8. **Thế chấp / xóa thế chấp KHÔNG phải sự kiện chuyển chủ.** Bẫy lớn nhất của bài toán chủ cuối. §Phase 2.
9. **Chủ cuối có thể NHIỀU người** từ một sự kiện; và có thể nhiều sự kiện chuyển chủ nối tiếp → phải xếp thứ tự rồi lấy sự kiện chuyển chủ **cuối cùng**. §Phase 2.
10. **Không có biến động chuyển chủ → chủ cuối = `Chủ sử dụng` trên giấy** (`nguon: "giay_goc"`). Luôn có giá trị, không bao giờ rỗng. §Phase 2.
11. **ONT vs ODT quyết theo địa chỉ thửa** — đã chốt: có `phường`/`thị trấn` → **ODT (192)**; là `xã` → **ONT (191)**. §Phase 1.
12. **Bảng alias MĐSD dựng từ dữ liệu thật (Phase 0.3), không đoán.** Fuzzy chỉ là lưới cuối có ngưỡng; dưới ngưỡng = `None` + cờ rà tay, **không bao giờ đoán bừa ra mã**. §Phase 1.
13. **Hybrid regex → VLM text-only cho bóc chủ.** Call text thuần rẻ hơn extract ảnh nhiều bậc (extract 70s là do vision token) → chạy được trên 600k mà không cạnh tranh GPU với pipeline chính. §Phase 2.

---

## Context

600k giấy đã trích xuất nằm trong AI-HUB. Ba việc cần làm:

1. **Rà soát lại phần cắt GCN** — file **>5 trang** đang bị cắt sai một số.
2. **Chủ cuối** — lọc từ biến động ra chủ sở hữu mới nhất thành một trường riêng, migrate cho 600k, rồi bật cho các lần chạy sau.
3. **Mục đích sử dụng** — chuẩn hóa `Loại mục đích` (text tự do từ VLM) về danh mục `LOAI_MDSDD` 80 mã (`id` / `ky_hieu_muc_dich` / `ten_muc_dich`).

## Nguyên tắc lõi

Việc 2 và 3 **không cần chạy lại model**: dữ liệu nguồn (`Biến động`, `Mục đích sử dụng`) đã nằm sẵn trong `gcn.extractions`. Biến đổi thuần trên dữ liệu có sẵn → an toàn, lặp lại được, không rủi ro pipeline.

Việc 1 cũng **đo được không cần GPU** (điểm 3) — chỉ bước xác nhận nguyên nhân mới cần nhìn lại ảnh.

---

## Hiện trạng cắt

### Phạm vi: chỉ file >5 trang

[`run_job.py:115`](backend/app/worker/run_job.py#L115):

```python
if n <= DETECT_MIN_PAGES:      # = 5
    return [list(range(n))]    # 1 giấy — ĐÚNG Ý ĐỒ, chốt, không đụng
```

Bộ GCN ≤5 trang là 1 giấy — quy tắc nghiệp vụ đã xác nhận (điểm 2). Bench cho thấy ~97% file rơi vào nhánh này và **chúng ổn**.

Nhánh còn lại (>5 trang) đi qua `classify_page` từng trang → [`_groups_from_roles`](backend/app/worker/run_job.py#L82-L103). **Đây là nơi cắt sai.** Tuy chỉ ~2–3% số file, ở 600k vẫn là **hàng chục nghìn hồ sơ**, và đúng là nhóm hồ sơ phức tạp nhất (nhiều GCN, trang bổ sung).

### Lỗi 1 — `other` đóng nhóm → cắt cụt GCN, mất trang

Nhánh `else` của `_groups_from_roles` (gồm `role == "other"`) đặt `cur = None`. Trang trắng giữa bộ — **mặt trái tờ gấp đôi, rất phổ biến** theo chính mô tả trong `detect_system_prompt` — nếu bị `classify_page` gán `"other"` sẽ **đóng nhóm giữa chừng**: mọi trang sau bị bỏ khỏi mọi nhóm, không log, không đếm, không cờ.

Đây là **nghi phạm số 1** cho "cắt sai" ở file dài: file càng nhiều trang, xác suất dính ≥1 trang bị gán `other` càng cao.

### Lỗi 2 — `content` lạc bị bỏ im lặng

Cùng nhánh: `content` xuất hiện trước khi có `cover` nào bị loại **không để lại dấu vết**. Chủ ý ban đầu đúng (tránh chế GCN giả từ trang phụ trợ) nhưng không quan sát được — không biết đang mất bao nhiêu trang.

### Lỗi 3 — gộp nhiều GCN vào một nhóm

Nếu `classify_page` gán nhầm một tờ bìa thành `content`, hai GCN bị gộp làm một nhóm → 1 lần `extract` gộp → `result["Đăng ký"]` có 2 entry nhưng `_build_cuts` chỉ sinh **1 file cắt**; [`_entry_sph`](backend/app/worker/run_job.py#L185) lấy **Số phát hành đầu tiên** đặt tên → GCN thứ hai mất file cắt riêng và mất định danh.

**Dấu hiệu này bắt được miễn phí** từ dữ liệu đã có (điểm 3): một record mà `Đăng ký` chứa ≥2 Số phát hành khác nhau ⇒ chắc chắn gộp nhầm.

### Lỗi 4 — `verify_split` là code chết

[`detect_gcn.py:40`](backend/src/extentions/multimodal/detect_gcn.py#L40) đã viết đầy đủ (kèm `verify_split_system_prompt`) nhưng **không call site nào trong `run_job.py`**. Đúng ra đây là lưới an toàn cho chính lỗi 3.

---

## Smoke test — khung chung (điểm 4)

Theo house-style [`datalens-agent/docs/ecosystem/testing-and-eval.md`](/Users/bags/prj/datalens-agent/docs/ecosystem/testing-and-eval.md). **Không dùng pytest.** Kỷ luật verify là một phần thiết kế, không phải bước phụ.

### Máy dev NO-RUN

Máy dev **chỉ compile**, không chạy (`src.*` không resolve, thiếu deps, không có Mongo):

```
python -m compileall backend/app/scripts/<file>.py
```

Logic chuỗi thuần (regex bóc chủ, chuẩn hóa MĐSD) tách khỏi `src.*` thì verify được bằng **script standalone** (bản sao pure, không import repo). Mọi thứ khác chạy **TRONG CONTAINER máy run**.

### Tầng PURE — logic thuần, stdlib, không infra

Embed `__main__` ngay trong module (không phải CLI vận hành — `normalize_dang_ky.py` "không CLI" nói về argparse; block smoke vẫn hợp lệ):

```
docker compose exec api python -m src.extentions.multimodal.mdsdd
docker compose exec api python -m src.extentions.multimodal.chu_cuoi
```

`assert …, "KILL [n] …"`. Không cần Mongo, không cần GPU.

### Tầng E2E — Mongo thật

**Runner tập trung** `backend/app/scripts/smoke.py`, nhiều stage:

```
docker compose exec api python -m app.scripts.smoke <stage> [<stage2> …]
```

Thêm khả năng ⇒ **thêm 1 stage** + đăng ký `_STAGES` + dọn trong `cleanup()`. **KHÔNG** tạo `smoke_X.py` rời cho tính năng đã có stage.

**Gate hạ tầng:** stage tự `skip` khi env vắng (không nối được Mongo) → phần PURE luôn chạy, phần I/O chỉ chạy khi có store thật. Stage đọc-only, `--dry-run` mặc định → `cleanup()` không có gì phải dọn; stage nào ghi thì bắt buộc tự dọn.

**Docstring mỗi stage PHẢI ghi lệnh `docker compose exec …`**, không ghi `python …` trần.

### Stage + tiêu chí KILL

| Stage | Tầng | KILL nếu |
|---|---|---|
| `mdsdd` | PURE | mã trả về ∉ `LOAI_MDSDD` · `method` ngoài tập cho phép · `text` rỗng mà vẫn ra mã · `"Đất ở"`+`phường` **không** ra ODT(192) · `"Đất ở"`+`xã` không ra ONT(191) |
| `chu_cuoi` | PURE | `chu` rỗng · text chỉ `thế chấp` mà **đổi chủ** · ca 2-chủ mẫu không tách đủ 2 người · `Số giấy tờ` ≠ 9/12 chữ số · bắt nhầm số hồ sơ `9186/2004/QĐCS…` thành CCCD |
| `cut_group` | PURE | trang thuộc >1 nhóm · nhóm rỗng · nhóm không bắt đầu bằng `cover` · **tổng trang phủ GIẢM** so với logic cũ trên cùng chuỗi nhãn |
| `mdsdd_real` | E2E | trên 20 doc thật: có mã ngoài danh mục · tỉ lệ map được < ngưỡng chốt từ 0.3 |
| `chu_cuoi_real` | E2E | trên 20 doc thật: có `chu` rỗng · `Số giấy tờ` sai độ dài |
| `cut_audit` | E2E | bất biến audit tự mâu thuẫn (trang rớt < 0 · nhóm trùng trang) — bảo vệ chính script đo |
| `backfill_idem` | E2E | chạy 2 lần cho kết quả **khác nhau** · `--dry-run` làm đổi dù chỉ 1 doc |

`cut_group` dòng in đậm là **KILL quan trọng nhất toàn plan**: mọi sửa `_groups_from_roles` phải chứng minh không làm rớt thêm trang.

**KILL đỏ → dừng, sửa GỐC**, không vá vội.

---

## Phase 0 — ĐO trước, sửa sau

Không đổi một dòng code production. Đặt tại `backend/app/scripts/`.

### 0.1a `audit_cut_offline.py` — đo thiệt hại cắt, KHÔNG GPU, phủ 100% (điểm 3)

Chỉ đọc `extractions` + `page_count`. Chạy được trên cả 600k. Đây là script **quan trọng nhất** vì nó cho con số thiệt hại thật, không phải suy đoán từ mẫu.

Với mỗi doc có `page_count > 5`:

| Chỉ số | Cách tính | Phơi lỗi |
|---|---|---|
| **Trang bị rớt** | `set(range(page_count)) − ⋃ page_indices` | **Lỗi 1 + 2** — đây là thước đo trực tiếp của thiệt hại |
| **Nhóm gộp nhầm** | record có ≥2 Số phát hành khác nhau trong `Đăng ký` | Lỗi 3 |
| **Nhóm 1 trang** | `len(page_indices) == 1` ở file dài | nghi bìa đứng lẻ vì trang sau bị gán `other` |
| **Nhóm không Số phát hành** | `_entry_sph` → `None` | nghi cover sai |
| **Rớt đuôi** | trang rớt nằm liền mạch tới hết file | chữ ký của lỗi 1 (`other` đóng nhóm) |

Đồng thời: **phân bố `page_count` trên toàn 600k** (aggregation, `page_count` là field sẵn có) → biết chính xác blast radius của nhánh >5 trang.

Xuất CSV danh sách `gcn_id` nghi ngờ, sắp theo số trang rớt giảm dần → làm đầu vào cho 0.1b.

### 0.1b `audit_cut_vlm.py` — xác nhận nguyên nhân, có GPU, mẫu nhỏ

Lấy ~100 doc **từ đầu danh sách nghi ngờ của 0.1a** (không sample mù — nhắm thẳng ca bệnh).

- Tải PDF gốc từ MinIO + render **đúng tham số production** (`dpi=200`, `max_img_size=2000` — không hạ, ảnh hạ nét làm nhãn sai).
- Chạy lại `classify_page` từng trang, **in ra chuỗi nhãn đầy đủ** (vd `cover content other content content`).
- Đối chiếu chuỗi nhãn với nhóm đã lưu → chỉ đích danh: rớt vì `other` giữa bộ? vì `content` lạc? vì bìa bị gán `content`?

Tận dụng khung sẵn có của [`bench_realpath.py`](backend/app/scripts/bench_realpath.py) (sample Mongo + tải MinIO + render + đo) nên viết nhanh.

**Đầu ra quyết định Phase 3:** tỉ lệ mỗi loại nguyên nhân → biết sửa `other` hay sửa prompt `classify_page` hay cả hai.

### 0.2 `audit_bien_dong.py` — biến động trông ra sao?

Quét toàn bộ 600k, **chỉ đọc `extractions`** — không GPU, không MinIO. Thống kê:
- % giấy có ≥1 biến động; phân bố số biến động/giấy (avg/p50/p90/max)
- **tần suất từ khóa**: `chuyển nhượng`, `tặng cho`, `thừa kế`, `chuyển quyền`, `nhận chuyển nhượng`, `thế chấp`, `xóa thế chấp`, `đính chính`, `cấp đổi`, `thay đổi diện tích`
- phân bố định dạng `Thời gian` (bao nhiêu parse ra ngày, bao nhiêu rác)
- % biến động chứa ≥1 số dạng CCCD/CMND (9 hoặc 12 chữ số)
- **in 50 `Nội dung biến động` dài nhất** để mắt người đọc — đầu vào thiết kế regex

→ Quyết định: regex thuần đủ chưa, hay cần nhánh VLM text-only.

### 0.3 `audit_mdsdd.py` — thực tế có bao nhiêu chuỗi mục đích?

Quét 600k, gom **tất cả giá trị `Loại mục đích` distinct + tần suất**, xuất CSV sắp giảm dần.

Kinh nghiệm dữ liệu loại này: vài trăm chuỗi phủ ~99%. Bảng alias Phase 1 dựng **từ file này**, không đoán (điểm 12). Đo thêm: % chuỗi có ký hiệu trong ngoặc `(ONT)`, % là `"Đất ở"` trần (cần luật ONT/ODT).

**Ràng buộc chung 0.1a/0.2/0.3:** cursor streaming `.batch_size(500)`, projection hẹp, **không `to_list(length=None)`** (điểm 7).

---

## Phase 1 — Mục đích sử dụng chuẩn hóa

Module thuần `backend/src/extentions/multimodal/mdsdd.py` — cùng style `normalize_dang_ky.py`: pure function, **không Mongo I/O, không CLI**, để pipeline và backfill dùng chung không circular import.

```python
LOAI_MDSDD: list[dict]                        # 80 mục: {id, ky_hieu_muc_dich, ten_muc_dich}
def map_muc_dich(text: str, dia_chi: str = "") -> dict | None
    # → {"id": 191, "ky_hieu": "ONT", "ten": "Đất ở tại nông thôn",
    #    "method": "ky_hieu_ngoac|ky_hieu_token|ten_exact|alias|fuzzy",
    #    "score": 1.0, "ambiguous": False}
```

### Thứ tự match (precision giảm dần)

1. **Ký hiệu trong ngoặc** — `"Đất ở tại nông thôn (ONT)"` → `ONT`. Chính xác nhất.
2. **Ký hiệu đứng riêng thành token** — `"ONT"`, `"CLN 200"`. Cẩn thận false-positive với chữ thường.
3. **Khớp tuyệt đối `ten_muc_dich`** sau chuẩn hóa: bỏ dấu ([`app/vn_text.py::strip_diacritics`](backend/app/vn_text.py) đã có) + lower + gộp khoảng trắng.
4. **Bảng alias** — dựng từ output 0.3.
5. **Fuzzy (rapidfuzz) có ngưỡng** — dưới ngưỡng trả `None` + cờ rà tay. **Không đoán bừa** (điểm 12).

### Luật ONT vs ODT (đã chốt — điểm 11)

`"Đất ở"` trần không phân biệt được **ONT (191)** và **ODT (192)**. Suy từ `Thửa đất.Địa chỉ`:

| Địa chỉ thửa chứa | → Mã |
|---|---|
| `phường` hoặc `thị trấn` | **ODT (192)** |
| `xã` | **ONT (191)** |
| không xác định được | `None` + `ambiguous: True` → hàng đợi rà tay |

**Chi tiết triển khai:** địa chỉ VN viết nhỏ → lớn. Nhiều đơn vị xuất hiện thì lấy **occurrence ĐẦU TIÊN** (cấp hành chính trực tiếp của thửa). So khớp sau khi bỏ dấu để bắt cả `Phường`, `P.`, `TT.`, `Thị Trấn`.

### Cổng smoke

- **PURE** `python -m src.extentions.multimodal.mdsdd` — bảng case phủ cả 5 `method` + 3 nhánh ONT/ODT/ambiguous.
- **E2E** `python -m app.scripts.smoke mdsdd_real` — 20 doc thật.

Xanh PURE mà chưa chạy `mdsdd_real` = **chưa xong**.

---

## Phase 2 — Chủ cuối

Module thuần `backend/src/extentions/multimodal/chu_cuoi.py`. Ba bước.

### Bước 1 — Phân loại biến động

| Nhóm | Từ khóa | Đổi chủ? |
|---|---|---|
| Chuyển chủ | `tặng cho`, `chuyển nhượng`, `nhận chuyển nhượng`, `thừa kế`, `chuyển quyền`, `cho tặng` | ✅ |
| **KHÔNG** đổi chủ | `thế chấp`, `xóa thế chấp`, `xoá đăng ký thế chấp`, `đính chính`, `cấp đổi`, `cấp lại`, `thay đổi diện tích`, `chuyển mục đích sử dụng` | ❌ |

**Bẫy lớn nhất** (điểm 8): thế chấp có tên ngân hàng + rất nhiều số hiệu, regex ngây thơ sẽ bóc ngân hàng thành chủ mới. **Danh sách phủ định kiểm TRƯỚC danh sách khẳng định.**

### Bước 2 — Xếp thứ tự, lấy sự kiện cuối

- Parse `Thời gian` → date; sort tăng dần.
- Hỏng/thiếu → **giữ nguyên thứ tự mảng** (thứ tự đọc trên giấy; `normalize_dang_ky._dedup_bien_dong` bảo toàn thứ tự).
- Lấy **sự kiện chuyển chủ CUỐI CÙNG** (điểm 9).
- Không có sự kiện chuyển chủ → `nguon: "giay_goc"`, chủ cuối = `Chủ sử dụng` (điểm 10).

### Bước 3 — Bóc chủ từ text tự do

Ca thực tế phải xử lý đúng:

> "Tặng cho ông Lý Thái Dũng, CCCD số 001064014946, HKTT tại Căn hộ số 103 - Nhà B3, phường Nghĩa Tân, quận Cầu Giấy, thành phố Hà Nội và bà Nguyễn Thị Thu ...., theo hồ sơ số 9186/2004/QĐCS 20557/2004"

→ **2 chủ**: Lý Thái Dũng (CCCD 001064014946, địa chỉ …) và Nguyễn Thị Thu (…).

**Hybrid (điểm 13):**
- **Regex trước**: tách theo `và ông/bà`; bắt `CCCD|CMND|CMT|căn cước|chứng minh` + số 9/12 chữ số; bắt `HKTT tại|địa chỉ|thường trú` → cụm địa chỉ tới dấu phân cách kế.
- **VLM text-only cho case regex không chắc** (không tách được người, hoặc số chủ ≠ số CCCD tìm thấy). Prompt text thuần, **không ảnh** → rẻ hơn extract nhiều bậc, không cạnh tranh GPU với pipeline chính.
- Tải thật do **0.2** trả lời trước khi cam kết.

**Cảnh giác:** `theo hồ sơ số 9186/2004/QĐCS 20557/2004` chứa số dài — loại trước khi bắt CCCD (ràng buộc đúng 9 hoặc 12 chữ số liền, có từ khóa dẫn ngay trước).

### Schema trường dẫn xuất

```python
"chu_cuoi": {
  "algo_version": 1,
  "nguon": "bien_dong" | "giay_goc",
  "bien_dong_index": 3,          # null nếu nguon = giay_goc
  "thoi_gian": "12/05/2004",
  "loai": "tang_cho",
  "chu": [
    {"Tên chủ": "", "Loại giấy tờ": "", "Số giấy tờ": "",
     "Địa chỉ": "", "Năm sinh": "", "Giới tính": ""}
  ],
  "confidence": "cao" | "thap",  # thap → hàng đợi hậu kiểm
}
```

Đặt song song `extractions`, cùng cấp `gcn_rows` (điểm 5). Một `chu_cuoi` cho mỗi entry `Đăng ký`.

### Cổng smoke

- **PURE** `python -m src.extentions.multimodal.chu_cuoi` — ca 2-chủ ở trên · ca chỉ thế chấp (không đổi chủ) · ca nhiều lần chuyển chủ nối tiếp · ca không biến động · ca `Thời gian` rác · ca số hồ sơ dài không được bắt thành CCCD.
- **E2E** `python -m app.scripts.smoke chu_cuoi_real` — 20 doc thật.

Regex bóc chủ là logic chuỗi thuần → **verify được bằng script standalone trên máy dev** trước khi đẩy lên container.

---

## Phase 3 — Sửa cắt + bật cho lần chạy sau

**Chỉ khởi động sau khi 0.1a + 0.1b có số liệu** (điểm 3). Nội dung theo nguyên nhân đo được:

- **Lỗi 1** — `other` **không đóng nhóm** nữa: chỉ loại trang đó, giữ nhóm mở. Phân biệt "trang trắng" với "tài liệu khác loại" — hoặc tách nhãn ở `classify_page`, hoặc đơn giản hơn: chỉ đóng nhóm khi gặp **≥2 `other` liên tiếp**. Chọn phương án nào do 0.1b quyết.
- **Lỗi 2** — đếm + ghi trang bị rớt vào record (`dropped_pages`) để quan sát được từ nay về sau.
- **Lỗi 3 + 4** — bật `verify_split` cho nhóm nghi ngờ: nhóm dài bất thường, hoặc extract trả **>1 Số phát hành trong cùng nhóm** (tín hiệu rõ nhất, lấy free từ dữ liệu đã có).
- **Không đụng** nhánh ≤5 trang (điểm 2).

**Cổng smoke Phase 3 — nghiêm nhất.** Stage PURE `cut_group` chạy logic cũ và mới trên **cùng tập chuỗi nhãn** lấy từ 0.1b (chuỗi nhãn là dữ liệu thuần, không cần GPU để replay):

```
docker compose exec api python -m app.scripts.smoke cut_group
```

KILL nếu **tổng số trang được phủ giảm** dù chỉ 1 trang, hoặc nhóm nào mất bìa. Chỉ khi xanh mới bench lại throughput ([`bench_realpath.py`](backend/app/scripts/bench_realpath.py)) rồi lên prod.

---

## Phase 4 — Lộ ra ngoài

Thêm vào [`flatten.COLUMNS`](backend/app/flatten.py#L19):

- `"Chủ cuối"` · `"Số chủ cuối"` · `"Nguồn chủ cuối"` (giấy gốc / biến động)
- `"Mã MĐSD"` · `"MĐSD chuẩn"`

Hiện trên UI bảng trích xuất + CSV export. Bản ghi `confidence: "thap"` / `ambiguous: True` phải **lọc được trên UI** để hậu kiểm có trọng điểm.

---

## Backfill 600k — ràng buộc bắt buộc

Pattern hiện có ([`backfill_chu_su_dung_norm.py:23`](backend/app/scripts/backfill_chu_su_dung_norm.py#L23)) nạp cả collection bằng `to_list(length=None)` → **OOM chắc chắn ở 600k**. Script mới **không được sao chép**. Yêu cầu:

- cursor stream `.batch_size(500)`, projection hẹp — không `to_list`
- `bulk_write` lô ~1000, `ordered=False`
- `--dry-run` / `--limit` / `--batch-id`
- **resumable**: lọc `chu_cuoi.algo_version` khác version hiện tại (điểm 6)
- **idempotent**: chạy 2 lần cùng kết quả — KILL của stage `backfill_idem` (§Smoke test)
- in tiến độ định kỳ + tổng kết (đã ghi / bỏ qua / lỗi / cần rà tay)

---

## Thứ tự thực thi

| Phase | Nội dung | GPU | Chặn bởi | Cổng ra (smoke phải xanh) |
|---|---|:---:|---|---|
| **0.3** | audit MĐSD distinct | – | – | CSV distinct |
| **0.2** | audit biến động | – | – | thống kê từ khóa |
| **0.1a** | audit cắt offline, 600k | – | – | `cut_audit` + **CSV nghi ngờ, số trang rớt** |
| **0.1b** | audit cắt VLM, mẫu ~100 | ✓ nhẹ | 0.1a | tỉ lệ theo nguyên nhân + tập chuỗi nhãn |
| **1** | module + backfill MĐSD | – | 0.3 | `mdsdd` + `mdsdd_real` + `backfill_idem` |
| **2** | module + backfill chủ cuối | – | 0.2 | `chu_cuoi` + `chu_cuoi_real` + `backfill_idem` |
| **3** | sửa cắt + bench lại | ✓ | **0.1a+0.1b** | `cut_group` (KILL: giảm trang phủ) |
| **4** | cột UI/CSV | – | 1, 2 | – |

**0.3, 0.2, 0.1a chạy được ngay và song song** — cả ba đều không GPU, không MinIO, chỉ đọc Mongo. Riêng **0.1a là câu trả lời trực tiếp cho "cắt sai bao nhiêu"** mà không tốn một call VLM nào.
