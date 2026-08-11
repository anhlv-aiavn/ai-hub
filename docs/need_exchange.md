# Cần trao đổi / làm rõ với khách hàng

> Nơi ghi các điểm **cần dựa vào tài liệu khách hàng cung cấp** để đặt câu hỏi và làm rõ trước
> khi quyết định thiết kế/nghiệm thu. Mỗi mục: **câu hỏi**, **vì sao cần**, **ảnh hưởng nếu chưa
> rõ**, **trạng thái** (❓ chờ hỏi · 💬 đã hỏi chờ trả lời · ✅ đã chốt).
>
> Khách tham chiếu: **VP Đăng ký đất đai TP Hà Nội**. Cập nhật khi có tài liệu/biên bản.

---

## A. Phạm vi dữ liệu & chất lượng nguồn

### EX-1 · ❓ Trường dữ liệu bắt buộc & tiêu chí "đạt" của một hồ sơ
- **Hỏi**: Danh mục trường **bắt buộc** phải bóc đúng (Số phát hành, Số vào sổ, chủ sử dụng,
  thửa/tờ bản đồ, diện tích, mục đích sử dụng, biến động, chủ cuối…)? Trường nào chỉ "có thì tốt"?
- **Vì sao**: quyết định tiêu chí eval độ chính xác + ngưỡng nghiệm thu; ảnh hưởng prompt & hậu kiểm.
- **Nếu chưa rõ**: eval không có chuẩn chấm; tranh cãi lúc nghiệm thu.

### EX-2 · ❓ Chất lượng & quy ước đặt tên file nguồn trên MinIO
- **Hỏi**: Cấu trúc thư mục/quy ước tên (`/{đơn vị}/{HSG-…}/…pdf`) có tài liệu chuẩn không? Có cam
  kết mọi key trong danh sách đều tồn tại file thật? Tỉ lệ scan mờ/nghiêng/thiếu trang ước tính?
- **Vì sao**: liên quan trực tiếp **OPS-1 (NoSuchKey)** — hiện có hồ sơ báo "không có file thật".
- **Nếu chưa rõ**: không phân biệt được "mất file" (bỏ qua) vs "lệch prefix" (bug cần sửa).

### EX-3 · ❓ "Không có GCN trong hồ sơ" (no_gcn) xử lý thế nào về nghiệp vụ
- **Hỏi**: Hồ sơ mà hệ thống không thấy bìa GCN nào — khách muốn coi là hợp lệ (bỏ qua), hay cần
  người soi lại thủ công 100%?
- **Vì sao**: hiện tách trạng thái `no_gcn` khỏi "Lỗi"; cần khớp kỳ vọng nghiệp vụ.

## B. Quy tắc nghiệp vụ

### EX-4 · ❓ Định nghĩa "Số phát hành" là khóa gom — biên độ chấp nhận
- **Hỏi**: Có trường hợp GCN **không có** Số phát hành, hoặc nhiều tờ dùng chung? Quy tắc gom tờ bổ
  sung khi thiếu Số phát hành?
- **Vì sao**: `group_key`/gom nhóm dựa hoàn toàn vào Số phát hành; ca thiếu cần quy tắc dự phòng.

### EX-5 · ❓ Chính sách bản trùng nội dung (dup_suspect)
- **Hỏi**: Khi 2 bản scan cùng một GCN (khác tên file) → giữ cả hai, gộp, hay chọn bản tốt hơn?
  Ai quyết? Có được xóa bản thừa không?
- **Vì sao**: hiện chỉ **cảnh báo** (`dup_suspect`), chưa có chính sách gộp (OPS-3/N-05).

### EX-6 · ❓ "Chủ cuối" — quy tắc suy diễn từ chuỗi Biến động
- **Hỏi**: Tài liệu quy tắc xác định **chủ hiện tại** từ chuỗi biến động (chuyển nhượng/thừa
  kế/tặng cho…)? Ca mập mờ (`canh_bao`) khách muốn xử lý ra sao?
- **Vì sao**: `chu_cuoi` hiện REGEX-only + backfill LLM ca mập mờ; cần chuẩn nghiệp vụ để đúng.

### EX-7 · ❓ Bảng mã Mục đích sử dụng đất (MĐSD) chuẩn
- **Hỏi**: Khách dùng bảng mã MĐSD nào (phiên bản/thông tư)? Có file danh mục chính thức không?
- **Vì sao**: `gan_mdsdd` gắn mã theo bảng nội bộ; phải khớp bảng khách để đối chiếu đúng.

## C₁. QC Sync (F-16 — pipeline mới, `algorithm.md §9`)

### EX-12 · ❓ Chính sách với verdict `warn` của QC Sync
- **Hỏi**: File QC ra `warn` (đạt nhưng có cảnh báo — `CLIPPED_EDGE`/`GLARE`/`EXTREME_SKEW`…) hiện
  vẫn chạy OCR như `pass`. Khách có muốn tách riêng để người soi duyệt trước khi OCR không, hay
  chấp nhận chạy thẳng?
- **Vì sao**: ảnh hưởng thiết kế hàng chờ hậu kiểm riêng cho QC Sync (chưa có ở v1).
- **Nếu chưa rõ**: giữ hành vi v1 (warn = chạy thẳng OCR như pass).

### EX-13 · ❓ API key + endpoint thật của `qc-scanner-server`
- **Hỏi**: `192.168.120.9:5000` có phải endpoint thật trên máy serve? Ai cấp `QC_SCANNER_API_KEY`?
- **Vì sao**: code viết theo hợp đồng API đọc offline (`qc_scanner` repo), CHƯA test sống được từ
  môi trường phát triển (IP nội bộ không tới được) — cần xác nhận khi triển khai lên máy serve.
- **Nếu chưa rõ**: pipeline QC Sync sẽ lỗi 401/kết nối ngay khi bật, cần key thật trước khi dùng.

### EX-14 · ❓ Chu kỳ quét lại + chi phí full re-scan có chấp nhận được không
- **Hỏi**: Mặc định 300s/lần, MỖI LẦN liệt kê lại TOÀN BỘ prefix nguồn (dedup ở tầng ghi, không
  phải ở tầng liệt kê — S3 không hỗ trợ lọc theo thời gian sửa). Với thư mục rất lớn, chi phí liệt
  kê định kỳ có chấp nhận được không, hay cần đầu tư S3 event/webhook (nếu MinIO khách hỗ trợ)?
- **Vì sao**: quyết định P2 backlog "tối ưu discovery" có đáng làm không.

### EX-15 · ❓ Dùng ảnh đã nắn/cắt của QC cho OCR thay vì PDF gốc
- **Hỏi**: v1 OCR chạy trên PDF GỐC (xem quyết định `features_issues.md#qc-decide-raw-ocr`). Nếu
  ảnh đã nắn thẳng của `qc-scanner-server` cải thiện độ chính xác OCR rõ rệt, khách có ưu tiên đầu
  tư nâng cấp này không (cần eval tập vàng trước khi đổi mặc định — cùng nguyên tắc PERF-6)?

## C. Vận hành & nghiệm thu

### EX-8 · ❓ Chỉ tiêu hiệu năng cam kết (SLA throughput)
- **Hỏi**: Mốc "xong X hồ sơ trong Y ngày/giờ" khách kỳ vọng? Số GPU/máy vLLM khách cấp?
- **Vì sao**: định hướng ưu tiên tối ưu (Giai đoạn 1 roadmap) + kích cỡ hạ tầng.

### EX-9 · ❓ Quy trình & phân quyền hậu kiểm
- **Hỏi**: Ai được duyệt/không duyệt? Có cần 2 cấp kiểm? Tiêu chí "đã hậu kiểm" tính năng suất?
- **Vì sao**: ảnh hưởng UI hậu kiểm, phân quyền lô, thống kê người duyệt.

### EX-10 · ❓ Định dạng bàn giao kết quả cuối
- **Hỏi**: Khách nhận kết quả dạng nào — CSV/Excel theo mẫu? File PDF đã cắt + đổi tên? Đẩy ngược
  vào hệ thống nào của khách? Có schema/mẫu cột cố định?
- **Vì sao**: quyết định export (F-07/F-08) có đúng mẫu; tránh làm lại lúc bàn giao.

### EX-11 · ❓ Bảo mật/lưu trữ dữ liệu nhạy cảm (CMND/CCCD, thông tin cá nhân)
- **Hỏi**: Yêu cầu bảo mật với dữ liệu định danh chủ sử dụng? Thời hạn lưu, quyền truy cập, xóa?
- **Vì sao**: dữ liệu chứa số giấy tờ/địa chỉ cá nhân — cần chính sách rõ để tuân thủ.

---

## Cách dùng file này
- Trước mỗi buổi làm việc với khách: lọc mục ❓, chuẩn bị câu hỏi + tài liệu cần xin.
- Sau khi có câu trả lời: đổi trạng thái ✅ + ghi **quyết định đã chốt** (kèm ngày, nguồn tài liệu).
- Quyết định chốt mà ảnh hưởng code → tạo issue/feature tương ứng trong `features_issues.md`.
