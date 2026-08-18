# HANDOFF — mở rộng AI-HUB nhận 3 loại giấy mới

> Ghi lại để phiên sau vào việc được ngay, không phải hỏi lại những gì đã chốt.
> Nhánh: **`doc-types-ddk-kqdk-pcctt`** (đã push, chưa merge vào `anhlv`).
> Cập nhật: 2026-08-18.

---

## 1. Yêu cầu gốc

AI-HUB đang chạy production cho **GCN** (giấy chứng nhận QSDĐ). Khách cần nhận thêm
3 loại đầu vào, phân biệt bằng một **cờ `doc_type`** trên API:

| mã | tên chính xác trên giấy | ghi chú |
|---|---|---|
| `gcn` | Giấy chứng nhận QSDĐ | đang chạy production, **KHÔNG được đổi hành vi** |
| `ddk` | ĐƠN ĐĂNG KÝ ĐẤT ĐAI, TÀI SẢN GẮN LIỀN VỚI ĐẤT (Mẫu số 15 + 15a/15b/15c) | |
| `kqdk` | GIẤY XÁC NHẬN ĐĂNG KÝ ĐẤT ĐAI (Chi nhánh VPĐK cấp) | giá trị dữ liệu cao nhất |
| `pcctt` | PHIẾU THU THẬP THÔNG TIN ĐẤT ĐAI | viết tay 100% |

Mẫu thật: `tmp/Phiếu CCTT - Đơn ĐK - Kết quả ĐK/{Phiếu CCTT,Đơn ĐK,Kết quả ĐK}`
(30 file mỗi loại, `/tmp` đã gitignore).

---

## 2. Quyết định đã chốt với khách — ĐỪNG hỏi lại

1. **`doc_type` ở mức lô, ghi đè được từng file.** Lưu lên từng doc `gcn`, không chỉ lên `batch`.
2. **Một file = MỘT hồ sơ.** Trang còn lại là tài liệu đính kèm (CCCD, sơ đồ kỹ thuật, ảnh chuyển khoản, giấy viết tay cũ) → **lọc bỏ**, chỉ extract trang biểu mẫu.
3. **Lưu chung collection `gcn`**, thêm field `doc_type` (không tách collection mới).
4. **Chưa cần đối soát chéo** giữa các loại; vẫn lưu sẵn khóa để sau bật lên không phải chạy lại kho.
5. **Khóa nghiệp vụ = SOURCE KEY** (đường dẫn MinIO / tên tệp), KHÔNG suy từ nội dung — ddk/pcctt không in số hiệu nào, và chữ viết tay không đáng làm khóa.
6. **Loại trang trùng** trước khi gọi VLM.

---

## 3. Ba phát hiện từ khảo sát 90 file thật (đã định hình thiết kế)

1. **Thứ tự trang không đáng tin.** `Đơn ĐK/1054768.pdf`: Mẫu 15 mặt trước (tr.1) → 15a (tr.2) → 15c (tr.3) → CCCD (tr.4–5) → **Mẫu 15 MẶT SAU (tr.6)**. Gom tuyến tính kiểu GCN sẽ cắt mất mục "Đề nghị cấp Giấy chứng nhận" + danh sách giấy tờ nộp kèm → phải gom **tất cả trang biểu mẫu vào một lần extract**.
2. **Trang bị nhân đôi.** `Kết quả ĐK/583572.pdf` 58 trang ≈ 29 tờ (bản màu + bản xám xoay 90°).
3. **Gần như toàn bộ dữ liệu cần bóc là chữ viết tay** → đây là trần chất lượng, không phải lỗi code.

---

## 4. Đã làm (6 commit trên nhánh)

```
63b21e1 feat(doc_type): 3 loại giấy mới
84a10c5 fix(smoke-e2e): sai mật khẩu/cổng không nhả traceback
d1f6e23 feat(smoke-e2e): --truc-tiep (không cần MinIO/Mongo/API)
186d7d1 fix(loi-vlm): "No connected db" không còn bị dán nhãn lỗi mạng
95d7c0c fix(probe_vlm): mọi call lỗi thì không kết luận
077834e fix(bóc tách): đo ĐỊNH DẠNG + chuẩn hoá ngày chữ + siết prompt chống bịa
```

**File mới**
- `backend/app/doc_types.py` — registry loại giấy (điểm khai báo DUY NHẤT). 2 chế độ gom: `GOM_GCN` (như cũ) và `GOM_MOT_HO_SO` (lọc `bieu_mau`/`dinh_kem`, gộp 1 nhóm). PURE smoke **42 KILL**.
- `backend/app/doc_prompts.py` — prompt extract + classify cho 3 loại (tầng app, KHÔNG đụng `src/extentions/multimodal/prompt.py` là vendor).
- `backend/app/anh_trung.py` — dedup trang bằng **dHash 16×16, ngưỡng 18**. PURE smoke **12 KILL**. *aHash 8×8 đã thử và LOẠI: hai tờ khác loại chỉ lệch 4/64 bit.*
- `backend/app/scripts/smoke_e2e_doc_types.py` — smoke E2E, 2 chế độ. Tự kiểm **40 KILL**.

**Sửa**: `routes/batches.py` · `routes/browse.py` · `routes/gcn.py` · `worker/import_job.py` · `worker/run_job.py` · frontend (`api.js`, `CreateBatch.jsx`, `MinioBrowser.jsx`, `style.css`) · `docs/algorithm.md §7b` · `README.md` · `.env.example`.

**Bất biến đã giữ**: nhánh `gcn` chạy y hệt trước (prompt/normalize vendor, summary cũ). `gan_mdsdd`/`chu_cuoi`/`va_sph_tu_ten_tep`/`_refresh_dup_group` gate về `dt.hau_xu_ly_gcn`. Khóa biểu mẫu đi đường riêng `extracted_keys`, **không** lọt vào index `extracted_so_phat_hanhs`. Doc cũ thiếu field → coi là `gcn`; lọc `?doc_type=gcn` khớp cả doc cũ.

---

## 5. Môi trường server (tecotec-4U10G-TURIN2)

| | |
|---|---|
| repo | `~/bags/ai-hub` (KHÁC máy dev `/Users/bags/prj/collab-prj/ai-hub`) |
| API | trong container `http://localhost:8000` · từ host `http://localhost:18002` |
| mẫu PDF | `./tmp` mount **read-only** vào container **api** tại `/work` (worker KHÔNG mount) |
| admin | `admin` / `admin123` — **mật khẩu tạm mình đặt lại, cần đổi** |
| khoá login | 5 lần sai → khoá 300s, ghi ở `user.locked_until` (collection tên **`user`** số ít, DB `aihub`) |
| vLLM | qua **litellm proxy** `http://192.168.120.10:30000/v1`, model `gemma-4-26B-A4B-NVFP4` |
| `VLLM_API_KEY` | phải là **master key** của proxy (đã đặt trong `.env`; proxy không nối DB nên virtual key sẽ lỗi `No connected db`) |
| MinIO đích | **ĐANG HỎNG — 502 Bad Gateway khi PutObject** (`storage.ai-hub.tumiki.org`) |

⚠️ MinIO đích chết nghĩa là **upload của pipeline GCN production cũng đang chết**, không riêng loại mới.
⚠️ `vllm-gemma4` không publish cổng ra host → **không thể** bỏ qua litellm proxy để trỏ thẳng vLLM.

---

## 6. Lệnh chạy

```bash
# PURE (không cần hạ tầng)
docker compose exec api python -m app.doc_types        # 42 KILL
docker compose exec api python -m app.anh_trung        # 12 KILL
docker compose exec worker python -m app.worker.run_job # 5 KILL
python3 backend/app/scripts/smoke_e2e_doc_types.py --tu-kiem  # 40 KILL

# E2E CHẠY THẲNG — không API, không MinIO, không Mongo  ← DÙNG CÁI NÀY khi MinIO chết
docker compose exec api python -m app.scripts.smoke_e2e_doc_types --truc-tiep \
    --loai pcctt "/work/Phiếu CCTT - Đơn ĐK - Kết quả ĐK/Phiếu CCTT" \
    --so-luong 5 --json /tmp/out

# cả 3 loại
docker compose exec api python -m app.scripts.smoke_e2e_doc_types --truc-tiep \
    --bo-ba "/work/Phiếu CCTT - Đơn ĐK - Kết quả ĐK" --so-luong 5 --json /tmp/out

# E2E qua API thật (chờ MinIO sống lại): bỏ --truc-tiep, thêm --api/-u/-p

# đo 1 call VLM
docker compose exec api python -m app.scripts.probe_vlm "<pdf>" --pages 0

# deploy
git pull && docker compose up -d --build api worker frontend
```

Smoke tách **2 tầng kết luận**: `KILL` = bất biến kỹ thuật vỡ (exit 1) · `độ điền` + `đúng định dạng` = chất lượng, không kill. Exit 2 = lỗi cấu hình.

---

## 7. Kết quả đo thực tế — `pcctt`, 5 file (2026-08-18)

**Cấu trúc: sạch.** 0 KILL. Bộ lọc trang chạy đúng: `1/4`, `1/1`, `1/1`, `1/2`, `2/4`. ~6s/file.

**Chất lượng sau khi siết prompt** (đối chiếu ảnh gốc `1319332`, `1218602`):

| | trước | sau |
|---|---|---|
| DT `4512,8` | 4512.**18** ✗ | **4512.8** ✓ |
| DT `204,6` | 204.**16** ✗ | **204.6** ✓ |
| Gốc `1319332` | "Đất do **Nhà nước giao**…" ✗✗ bịa | "**Do Bà Mẹ để lại**…" ✓ |
| Ngày ô trống | giữ cả câu "ngày .... tháng 7" ✗ | `''` ✓ |
| CCCD `001085.015.315` | ✗ | `001085015315` ✓ |

**Còn sai (chưa xử)**:
- Trường mô tả dài chưa trung thực: `1319332` đuôi bị lặp chữ + thêm cụm "cây lấy gỗ, cây cảnh quan, cây trồng khác" **không có trên giấy**, và **mất 3 câu cam đoan có thật** ("không có tranh chấp", "không vi phạm pháp luật đất đai", "không có giấy tờ về đất") — đây là căn cứ pháp lý, không phải văn thừa.
- Chữ số viết tay: ngày `10`→`20`; tờ bản đồ đọc `14a` (mình đọc ảnh là `166`); thửa `1218602` hai lần chạy ra `7` rồi `78`.

> **Cảnh báo cho phiên sau**: cách đọc ảnh của Claude KHÔNG phải điểm chuẩn. Chữ viết tay mấy phiếu này thật sự mờ nghĩa (`166` vs `14a`). Chỉ khách/dữ liệu địa chính mới chốt được. Đừng "sửa" prompt dựa trên phán đoán của mình về chữ viết tay.

---

## 7b. Kết quả đo — `ddk` + `kqdk`, mỗi loại 5 file (2026-08-18)

**Cấu trúc: sạch cả ba loại, 0 KILL / 15 hồ sơ.** Dự đoán "sẽ lộ lỗi cấu trúc" ở phụ lục 15a/15c và checkbox mục "Đề nghị" **không xảy ra** — gom trang một-hồ-sơ và bộ lọc `dinh_kem` chạy đúng trên cả đơn nhiều trang.

| loại | trang vào/tổng | giây/file | độ điền |
|---|---|---|---|
| `ddk` | 4/6 · 4/6 · 4/8 · 4/6 · 4/7 | 12–17 | 15/15 (4 file), 10/15 (1 file) |
| `kqdk` | 2/4 · 3/5 · 2/4 · 2/4 · 2/4 | 11–24 | 13/13 cả 5 |

`kqdk` là loại **sạch nhất** cho tới giờ: 100% điền và 100% đúng định dạng trên cả 3 trường có luật.

**Đã sửa từ lượt chạy này** (commit `906ab39`): bảng báo `Giấy tờ nhân thân` chỉ 40% đúng định dạng — **lỗi code, không phải lỗi model**. `_normalize_bieu_mau` bắt theo tên `"Số giấy tờ"`, nhưng Mẫu 15 mục 1b in là `"Giấy tờ nhân thân"`, nên CCCD trên `ddk` không hề được chuẩn hoá. Nay gom vào `_TEN_SO_GIAY_TO`, có KILL [17a] ghim lại. **CHƯA xác nhận được trên máy thật**: lượt đo lại chỉ `restart` chứ không `build`, nên vẫn là code cũ (xem §9). Phải build lại rồi chạy `ddk` lần nữa.

**Còn phải soi**:
- `1134303.pdf` (`ddk`) lệch hẳn phần còn lại: 10/15 trường, thiếu trọn cụm Thửa đất (Địa chỉ/Diện tích/Mục đích/Nguồn gốc) và chạy 12s trong khi các file kia 16–17s. Nghi trang chứa mục 2 bị bộ lọc xếp nhầm `dinh_kem` → mở `--json` xem `page_indices` trước khi động vào prompt.
- `1319369.pdf` (`pcctt`) `Số giấy tờ` sai định dạng: trường này CÓ được chuẩn hoá, nên số chữ số không phải 9/12 → hoặc model đọc sót/thừa chữ số, hoặc giấy ghi vậy thật. Phải đối chiếu ảnh.
- Chưa có con số độ chính xác **nội dung** cho `ddk`/`kqdk` — mới chỉ biết cấu trúc và định dạng đúng.

---

## 8. Việc tiếp theo, theo thứ tự

1. **`docker compose build api && docker compose up -d api`** rồi chạy lại `--bo-ba` để xác nhận `Giấy tờ nhân thân` lên 100% đúng định dạng, rồi mở `--json` soi hai ca nêu ở §7b (`1134303` thiếu cụm Thửa đất, `1319369` sai số chữ số CCCD).
2. **Định lượng độ chính xác**: ~20 hồ sơ, đối chiếu JSON ↔ ảnh gốc, đếm tỷ lệ trường sai. Con số này mới quyết được pipeline dùng được chưa. Đã đề xuất công cụ xuất HTML đặt ảnh trang cạnh JSON — **khách chưa trả lời**, hỏi lại trước khi làm.
3. **Nếu cần tăng độ chính xác**: lượt hai chỉ hỏi lại một trường (đúng bài `SPH_LUOT_HAI` của pipeline GCN — ít trường thì model soi kỹ hơn), ưu tiên "Nguồn gốc sử dụng" và các ô chữ số. **Chỉ làm sau khi có số liệu ở bước 2.**
4. **Sửa MinIO đích 502** rồi chạy E2E qua API thật (bỏ `--truc-tiep`) để phủ nốt: upload, hàng đợi Mongo, counter lô, cắt trang, SSE.
5. **Đổi mật khẩu admin** khỏi `admin123`; cân nhắc đổi master key litellm (đã lộ qua chat).
6. Merge nhánh vào `anhlv` khi khách duyệt.

---

## 9. Cạm bẫy đã gặp — đừng lặp lại

- `docker compose exec` + heredoc → **phải có `-T`**.
- Trong container API là cổng **8000**, không phải 18002; đường dẫn mẫu là **`/work/…`**, không phải `tmp/…`.
- `docker compose exec` dùng env của container **đang chạy** — sửa `.env` phải `docker compose up -d` mới nạp.
- **`api` dùng `build: ./backend`, code NẰM TRONG IMAGE.** `git pull` + `docker compose restart api` **KHÔNG** nạp code mới — phải `docker compose build api && docker compose up -d api`. Chỉ `./tmp` và `./cache_minio_index` là volume. Đã một lần đo lại sau khi sửa mà thực chất vẫn chạy code cũ, rồi suýt kết luận nhầm là bản sửa phản tác dụng (40% → 20%, thật ra chỉ là VLM chạy khác giữa hai lượt).
- Collection user tên **`user`** (số ít), DB `aihub`.
- **Ba bug "output nói dối" đã sửa trong phiên này** — cùng một loại lỗi, tốn ~1 giờ truy sai hướng:
  - `_friendly_vlm_error` dò chuỗi con `"connect"` → nuốt luôn `"No connected db"` của litellm proxy thành "lỗi mạng".
  - `probe_vlm` in kết luận về reasoning kể cả khi **cả 3 mode đều lỗi**, bảng trống trơn.
  - smoke nhả 30 dòng traceback cho lỗi sai mật khẩu.
  → Nguyên tắc rút ra: **thông báo/kết luận sai còn tệ hơn không có**. Thấy loại này thì sửa, kèm KILL cho chính nó.
- **`100% độ điền` không có nghĩa là đúng** — nó chỉ đếm ô khác rỗng. Lần đầu chạy pcctt ra 100% trong khi 3/4 trường kiểm được của `1319332` đều sai. Luôn mở `--json` đối chiếu ảnh.
