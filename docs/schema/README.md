# Schema trích xuất AI-HUB

Sinh tự động — **đừng sửa tay**. Sửa prompt trong `backend/app/doc_prompts.py`
(hoặc `backend/src/extentions/multimodal/prompt.py` cho `gcn`) rồi chạy lại:

```bash
python -m app.scripts.xuat_schema
```

## Bốn loại giấy

| `doc_type` | Tên giấy | Khoá bọc ngoài của `result` | Trạng thái | Schema |
|---|---|---|---|---|
| `gcn` | Giấy chứng nhận | `Đăng ký` | ✅ | [gcn.schema.json](gcn.schema.json) · [gcn.example.json](gcn.example.json) |
| `ddk` | Đơn đăng ký | `Đơn đăng ký` | ✅ | [ddk.schema.json](ddk.schema.json) · [ddk.example.json](ddk.example.json) |
| `kqdk` | Giấy xác nhận đăng ký | `Giấy xác nhận` | ⏸ tạm tắt | [kqdk.schema.json](kqdk.schema.json) · [kqdk.example.json](kqdk.example.json) |
| `pcctt` | Phiếu thu thập thông tin | `Phiếu thu thập` | ✅ | [pcctt.schema.json](pcctt.schema.json) · [pcctt.example.json](pcctt.example.json) |

`doc_type` là tham số của API, mặc định `gcn` nếu không truyền (giữ tương thích
với dữ liệu cũ). Giá trị lạ → HTTP 400.

Loại **tạm tắt** không gửi lên được nữa (API trả 400) nhưng schema vẫn giữ ở đây,
vì dữ liệu đã bóc trước đó vẫn nằm trong hệ thống và vẫn theo đúng cấu trúc này.

- `POST /v1/batches` — form field `doc_type`
- `POST /v1/browse/{source_id}/import` — JSON field `doc_type`
- `GET  /v1/gcn?doc_type=<ma>` — lọc theo loại

## Lấy dữ liệu ra

`GET /v1/gcn/{gcn_id}` trả về một hồ sơ. Phần cấu trúc theo loại giấy nằm ở
`extractions[].result`, và **đó là thứ các schema ở đây mô tả**:

```jsonc
{
  "_id": "…", "batch_id": "…", "filename": "…", "doc_type": "pcctt",
  "status": "done",                  // queued | running | done | error
  "group_key": "2026/Phieu CCTT/576827",
  "summary": { … },                 // tóm tắt phẳng, xem bên dưới
  "extractions": [
    {
      "page_indices": [0],            // trang (0-based) đã đưa vào VLM
      "page_count": 4,                // tổng số trang của PDF gốc
      "result": { … },               // ← THEO SCHEMA Ở ĐÂY
      "error": null,
      "skip_reason": null
    }
  ]
}
```

`summary` là bản phẳng dùng chung cho mọi loại giấy, để hiển thị bảng:
`khoa_chinh`, `so_phat_hanh`, `so_vao_so`, `ngay_cap`, `chu_su_dung[]`,
`to_ban_do[]`, `so_thua[]`, `gcn_count`. Với 3 loại biểu mẫu thì `khoa_chinh`
lấy từ **tên tệp/prefix nguồn**, không phải từ nội dung giấy — mấy loại này
không có số hiệu riêng nào đáng tin trên mặt giấy.

## Chỉ muốn dùng các trường của Giấy chứng nhận?

Không cần đọc schema riêng của từng loại. Với `ddk`/`kqdk`/`pcctt`, hệ thống map
sẵn sang **đúng hình dạng `gcn`** ở hai chỗ:

- `GET /v1/gcn/{id}` → trường **`dang_ky`**: `[{rec_index, page_indices, "Đăng ký": [...]}]`,
  khối `"Đăng ký"` theo đúng [gcn.schema.json](gcn.schema.json).
- `GET /v1/gcn/rows` và `export.csv` → cùng bộ cột phẳng như `gcn`, không phải xử lý riêng.

Bản map **tính lúc đọc**, nên luôn phản ánh dữ liệu đã hậu kiểm. `extractions[].result`
vẫn giữ nguyên khối gốc của từng loại giấy (dùng cho hậu kiểm và đối chiếu).

Những gì đơn/phiếu KHÔNG có thì để **rỗng, không bịa**: `Mã vạch`, `Ngày cấp`
(của giấy chứng nhận), `Biến động`, và `Số vào sổ` (trừ `kqdk` — văn bản này có
số vào sổ ĐKĐĐ thật). `Số phát hành` = **khóa nguồn** (đường dẫn/tên tệp trên kho
S3), vì mấy loại này không in số hiệu nào trên giấy.

## Ba điều bên nhận cần biết trước khi lập trình

**Ô trống trả `""`, không phải `null`.** Mảng rỗng là `[]`. Không có key nào bị
lược bỏ — cấu trúc luôn đủ trường như trong `*.example.json`.

**Giá trị đã qua chuẩn hoá, nhưng chuẩn hoá KHÔNG bao giờ ép.** Ngày, diện tích
và số giấy tờ được đưa về dạng chuẩn *khi đọc được*; đọc không ra thì **giữ
nguyên văn chữ trên giấy** chứ không trả rỗng. Xem `description` của từng trường
trong schema. Vì vậy đừng khai kiểu chặt (`date`, `number`) ở phía nhận — mọi
trường đều là chuỗi, kể cả trường ngày và trường số.

**Định dạng đúng không có nghĩa nội dung đúng.** Đây là dữ liệu viết tay được
model đọc; một giá trị hợp lệ vẫn có thể sai so với giấy. Luồng nghiệp vụ phải
có bước hậu kiểm, đừng coi kết quả là dữ liệu đã chốt.
