"""Prompt + schema cho các LOẠI GIẤY ngoài GCN.

Ba loại (khảo sát 90 file mẫu thật của khách, 2026-08-18):
  ddk   — ĐƠN ĐĂNG KÝ ĐẤT ĐAI, TÀI SẢN GẮN LIỀN VỚI ĐẤT (Mẫu số 15 + 15a/15b/15c)
  kqdk  — GIẤY XÁC NHẬN ĐĂNG KÝ ĐẤT ĐAI (Chi nhánh VPĐK đất đai cấp)
  pcctt — PHIẾU THU THẬP THÔNG TIN ĐẤT ĐAI

Để ở TẦNG APP (không phải `src/extentions/multimodal/prompt.py`) vì đó là vendor
giữ nguyên trạng từ `auto-detect-extract-gcn-vlm` — thêm loại giấy của AI-HUB vào
đó là tự tạo xung đột cho lần vendor cập nhật sau.

Đặc thù ĐO TỪ MẪU THẬT, quyết định cách viết prompt:
- Phần dữ liệu cần bóc gần như TOÀN BỘ là CHỮ VIẾT TAY (pcctt viết tay 100%).
  Prompt vì thế nhấn mạnh "chỉ đọc phần điền tay, bỏ phần chữ in sẵn của mẫu".
- Một file = MỘT hồ sơ, các trang còn lại là tài liệu đính kèm (CCCD, sơ đồ kỹ
  thuật, ảnh chuyển khoản, giấy viết tay cũ) → có prompt classify riêng để LỌC.
- Thứ tự trang KHÔNG đáng tin: mặt sau của Mẫu 15 có thể nằm sau cả tập đính kèm
  (file Đơn ĐK/1054768.pdf: mặt sau ở trang 6/6) → extract nhận CẢ TẬP trang biểu
  mẫu một lần, không gom theo vị trí.
"""

# ── Ràng buộc chung ─────────────────────────────────────────────────────────
_CHUNG = """- Return JSON ONLY. No explanation, no markdown.
- Output MUST match the structure EXACTLY. Do NOT add or remove fields.
- Thiếu thông tin → "" cho chuỗi, [] cho mảng. TUYỆT ĐỐI không bịa.
- Giữ nguyên tiếng Việt có dấu như trên giấy.
- Ngày tháng → dd/mm/yyyy. Diện tích → số, bỏ đơn vị m², dấu phẩy → dấu chấm.
- CHỈ trích phần ĐIỀN VÀO (chữ viết tay, chữ đánh máy điền thêm, ô được tick).
  Phần chữ IN SẴN của biểu mẫu là nhãn, KHÔNG phải dữ liệu — trừ khi nhãn in sẵn
  là một phần của giá trị (vd địa chỉ in sẵn "xã Trung Giã, thành phố Hà Nội" nối
  với phần thôn/xóm viết tay thành địa chỉ đầy đủ).
- Chữ viết tay khó đọc: đọc được bao nhiêu ghi bấy nhiêu, KHÔNG đoán thêm ký tự.
  Ô bỏ trống → "" (đừng chép nhãn in sẵn vào đó).
- CẤM THAY NỘI DUNG THẬT BẰNG CỤM MẪU. Các trường mô tả dài (nguồn gốc sử dụng
  đất, nội dung xác nhận) phải CHÉP NGUYÊN VĂN chữ trên giấy, kể cả khi câu văn
  lủng củng, sai chính tả hay thiếu ý. TUYỆT ĐỐI không viết lại thành công thức
  pháp lý quen thuộc ("Nhà nước giao", "Nhà nước công nhận quyền sử dụng đất",
  "cây lấy gỗ, cây cảnh quan, cây trồng khác"…) nếu chữ trên giấy không phải vậy.
  Ví dụ SAI đã gặp: giấy ghi "Do Bà mẹ để lại cho sử dụng vào mục đích trồng cây
  lâu năm" mà trả về "Đất do Nhà nước giao…" — hai nguồn gốc pháp lý khác hẳn
  nhau, ghi sai là làm hỏng hồ sơ. Đọc được đến đâu chép đến đó; chỗ không đọc
  nổi thì bỏ, KHÔNG chế thêm.
- DẤU PHẨY TRONG SỐ LÀ DẤU THẬP PHÂN, KHÔNG PHẢI CHỮ SỐ. "4512,8" là bốn nghìn
  năm trăm mười hai phẩy tám → trả "4512.8", KHÔNG phải "4512.18". "204,6" →
  "204.6", KHÔNG phải "204.16". Đọc kỹ phần sau dấu phẩy: diện tích thửa đất
  thường chỉ có MỘT chữ số thập phân.
- Số CC/CCCD là 9 hoặc 12 chữ số — không nhầm với số điện thoại, số thửa, mã vạch.
  Ô số giấy tờ chỉ ghi DÃY SỐ, không kèm chữ. Giấy viết "CCCD số 033064004050 cấp
  ngày 22/11/2021" thì trả "033064004050" — bỏ cả chữ "CCCD số" lẫn phần ngày cấp.
- Các ảnh đưa vào có thể là những trang KHÔNG liền nhau của cùng một hồ sơ (mặt
  sau của tờ khai có thể nằm ở ảnh cuối). Hãy ghép chúng thành MỘT hồ sơ duy nhất."""

_CLASSIFY_CHUNG = """Ảnh CÓ THỂ BỊ XOAY NGANG 90° (đọc cả khi xoay, đừng vì xoay mà cho là tài liệu khác loại).
Trả đúng một trong hai nhãn:
- "bieu_mau": trang thuộc chính tờ khai/giấy đang xét (kể cả mặt sau, phần ký, phụ lục bảng kê kèm theo mẫu).
- "dinh_kem": tài liệu ĐÍNH KÈM hoặc không liên quan — căn cước/CMND, hộ khẩu, sơ đồ/hồ sơ kỹ thuật thửa đất, ảnh chụp màn hình chuyển khoản/hoá đơn, giấy mua bán viết tay, hợp đồng, biên lai, Giấy chứng nhận, trang trắng.

Nghi ngờ → chọn "bieu_mau" (giữ trang, an toàn hơn là vứt). Chỉ trả JSON {"role": "..."}."""


# ═════════════════════════════════════════════════════════════════════════════
# ddk — ĐƠN ĐĂNG KÝ ĐẤT ĐAI, TÀI SẢN GẮN LIỀN VỚI ĐẤT (Mẫu số 15)
# ═════════════════════════════════════════════════════════════════════════════

ddk_extract_system_prompt = r"""Bạn là chuyên gia bóc tách hồ sơ đất đai Việt Nam. Các ảnh dưới đây là MỘT bộ ĐƠN ĐĂNG KÝ ĐẤT ĐAI, TÀI SẢN GẮN LIỀN VỚI ĐẤT (Mẫu số 15), có thể kèm các bảng phụ lục Mẫu 15a (danh sách người sử dụng chung), Mẫu 15b (danh sách thửa) và Mẫu 15c (danh sách tài sản).

""" + _CHUNG + r"""

Hướng dẫn từng mục (đánh số bám đúng biểu mẫu):
1. Người sử dụng đất, chủ sở hữu tài sản gắn liền với đất, người quản lý đất:
   - 'Họ và tên' (a), 'Giấy tờ nhân thân/pháp nhân' (b) — thường là dãy 12 số CCCD,
     'Địa chỉ' (c), 'Điện thoại' và 'Hộ thư điện tử' (d).
2. Thửa đất đăng ký:
   - 'Thửa đất số' (a), 'Tờ bản đồ số' (2.2), 'Địa chỉ' (b) — nối phần viết tay với
     phần phường/xã, thành phố in sẵn ở cuối dòng.
   - 'Diện tích' (c) kèm 'Sử dụng chung' và 'Sử dụng riêng' nếu có ghi.
   - 'Mục đích sử dụng' và 'Từ thời điểm' (d), 'Thời hạn đề nghị được sử dụng đất' (d),
     'Nguồn gốc sử dụng đất' (e).
   - 'Quyền với thửa liền kề' (g): số thửa, tờ bản đồ, của ai, nội dung quyền.
3. Nhà ở, công trình xây dựng: loại (a), diện tích xây dựng (b), diện tích sàn/diện
   tích sử dụng (c), sở hữu chung/riêng (d), số tầng - tầng nổi - tầng hầm (đ),
   nguồn gốc (e), năm hoàn thành xây dựng (g), thời hạn sở hữu đến (h), cam kết đủ
   điều kiện tồn tại (i) — ô có tick thì ghi true.
4. Đề nghị của người sử dụng đất — 4 ô tick, ghi true/false cho từng ô:
   'Đăng ký đất đai, tài sản gắn liền với đất' (a), 'Cấp Giấy chứng nhận' (b),
   'Ghi nợ tiền sử dụng đất' (c), và 'Đề nghị khác' (d) ghi nội dung nếu có.
5. 'Giấy tờ nộp kèm theo': liệt kê từng dòng (1)(2)(3).
Cuối đơn: 'Địa danh', 'Ngày ký', 'Người kê khai'.

Phụ lục (chỉ điền nếu ảnh có trang tương ứng, không có thì để mảng rỗng):
- Mẫu 15a 'Người sử dụng chung': mỗi DÒNG của bảng là một người — tên, năm sinh,
  loại giấy tờ, số, ngày cấp, cơ quan cấp, địa chỉ. Bỏ qua dòng trống/bị gạch chéo.
- Mẫu 15b 'Danh sách thửa': mỗi dòng một thửa.
- Mẫu 15c 'Danh sách tài sản': mỗi dòng một tài sản.

Trả ĐÚNG cấu trúc JSON sau:

{
  "Đơn đăng ký": {
    "Thông tin đơn": {
      "Kính gửi": "",
      "Địa danh": "",
      "Ngày ký": "",
      "Người kê khai": ""
    },
    "Người sử dụng đất": {
      "Họ và tên": "",
      "Giấy tờ nhân thân": "",
      "Địa chỉ": "",
      "Điện thoại": "",
      "Hộp thư điện tử": ""
    },
    "Thửa đất": {
      "Thửa đất số": "",
      "Tờ bản đồ số": "",
      "Địa chỉ": "",
      "Diện tích": "",
      "Sử dụng chung": "",
      "Sử dụng riêng": "",
      "Mục đích sử dụng": "",
      "Từ thời điểm": "",
      "Thời hạn đề nghị": "",
      "Nguồn gốc sử dụng": "",
      "Quyền với thửa liền kề": {
        "Thửa số": "",
        "Tờ bản đồ số": "",
        "Của": "",
        "Nội dung": ""
      }
    },
    "Nhà ở, công trình xây dựng": {
      "Loại": "",
      "Diện tích xây dựng": "",
      "Diện tích sàn": "",
      "Sở hữu chung": "",
      "Sở hữu riêng": "",
      "Số tầng": "",
      "Số tầng nổi": "",
      "Số tầng hầm": "",
      "Nguồn gốc": "",
      "Năm hoàn thành xây dựng": "",
      "Thời hạn sở hữu đến": "",
      "Cam kết đủ điều kiện tồn tại": ""
    },
    "Đề nghị": {
      "Đăng ký đất đai, tài sản": "",
      "Cấp Giấy chứng nhận": "",
      "Ghi nợ tiền sử dụng đất": "",
      "Đề nghị khác": ""
    },
    "Giấy tờ nộp kèm": [],
    "Người sử dụng chung": [
      {
        "Tên": "",
        "Năm sinh": "",
        "Loại giấy tờ": "",
        "Số giấy tờ": "",
        "Ngày cấp": "",
        "Cơ quan cấp": "",
        "Địa chỉ": ""
      }
    ],
    "Danh sách thửa": [
      {
        "Thửa đất số": "",
        "Tờ bản đồ số": "",
        "Địa chỉ": "",
        "Diện tích": "",
        "Mục đích sử dụng": "",
        "Thời hạn sử dụng": "",
        "Nguồn gốc sử dụng": ""
      }
    ],
    "Danh sách tài sản": [
      {
        "Loại": "",
        "Diện tích xây dựng": "",
        "Diện tích sàn": "",
        "Hình thức sở hữu": "",
        "Số tầng": "",
        "Nguồn gốc": "",
        "Thời hạn sở hữu": ""
      }
    ]
  }
}
"""

ddk_extract_user_prompt = """Các ảnh này là MỘT bộ đơn đăng ký (có thể gồm mặt trước, mặt sau và các bảng phụ lục, thứ tự trang không theo quy luật). Bóc tách thành một object JSON duy nhất theo đúng cấu trúc đã cho. Chỉ trả JSON."""

ddk_classify_system_prompt = """Bạn nhận MỘT ảnh là một trang trong bộ hồ sơ ĐƠN ĐĂNG KÝ ĐẤT ĐAI của Việt Nam.
Trang thuộc chính tờ đơn gồm: tờ có tiêu đề "ĐƠN ĐĂNG KÝ ĐẤT ĐAI, TÀI SẢN GẮN LIỀN VỚI ĐẤT" (Mẫu số 15); MẶT SAU của tờ đó (bắt đầu bằng các mục "e) Nguồn gốc", "4. Đề nghị của người sử dụng đất", "5. Những giấy tờ nộp kèm theo", phần cam đoan và chữ ký); và các bảng phụ lục ghi "Mẫu số 15a/15b/15c" ở góc trên bên phải (DANH SÁCH những người sử dụng chung / danh sách thửa / danh sách tài sản gắn liền với đất).

""" + _CLASSIFY_CHUNG

ddk_classify_user_prompt = """Trang này thuộc chính tờ đơn đăng ký (kể cả mặt sau hoặc bảng phụ lục Mẫu 15a/15b/15c), hay là tài liệu đính kèm? Chỉ trả JSON {"role": "..."}."""


# ═════════════════════════════════════════════════════════════════════════════
# kqdk — GIẤY XÁC NHẬN ĐĂNG KÝ ĐẤT ĐAI
# ═════════════════════════════════════════════════════════════════════════════

kqdk_extract_system_prompt = r"""Bạn là chuyên gia bóc tách hồ sơ đất đai Việt Nam. Các ảnh dưới đây là MỘT GIẤY XÁC NHẬN ĐĂNG KÝ ĐẤT ĐAI do Chi nhánh Văn phòng đăng ký đất đai cấp (văn bản đánh máy, có số hiệu, dấu và chữ ký).

""" + _CHUNG + r"""

Hướng dẫn từng mục (đánh số bám đúng văn bản):
Đầu văn bản: tên 'Cơ quan cấp' (góc trên trái, vd "Chi nhánh Văn phòng đăng ký đất
đai Hà Nội huyện Thanh Trì"), 'Số văn bản' (vd "108/GXN-VPĐKĐĐTT"), 'Địa danh' và
'Ngày ký'.
1. Người sử dụng đất — có thể nhiều người (1.1, 1.2, …), MỖI NGƯỜI LÀ MỘT ĐỐI TƯỢNG
   RIÊNG (vợ chồng cũng tách hai): họ tên, loại + số giấy tờ (CMND/CCCD), ngày cấp,
   nơi cấp, địa chỉ thường trú.
2. Đăng ký thửa đất: thửa đất số (2.1), tờ bản đồ (2.2), địa chỉ (2.3), diện tích /
   sử dụng riêng / sử dụng chung (2.4), mục đích sử dụng + từ thời điểm (2.5), thời
   hạn đề nghị sử dụng đất (2.6), nguồn gốc sử dụng đất (2.7 — đoạn dài, CHÉP
   NGUYÊN VĂN, đây là nội dung pháp lý quan trọng nhất, không tóm tắt).
3. Nội dung xác nhận của UBND xã/phường: nguồn gốc kê khai (3.1), nguồn gốc sử dụng
   đất (3.2), thời điểm sử dụng đất vào mục đích (3.3), thời điểm tạo lập tài sản
   gắn liền với đất (3.4), tình trạng tranh chấp (3.5), sự phù hợp với quy hoạch
   (3.6), nội dung khác (3.7). Chép nguyên văn từng mục.
4. 'Giấy tờ đã nộp': liệt kê từng gạch đầu dòng.
5. 'Ghi chú': liệt kê từng gạch đầu dòng; trong đó 'Số vào sổ ĐKĐĐ' tách riêng ra
   trường của nó nếu có ghi.
Cuối văn bản: 'Người ký' và 'Chức danh' (vd Giám đốc), 'Nơi nhận'.

Trả ĐÚNG cấu trúc JSON sau:

{
  "Giấy xác nhận": {
    "Thông tin văn bản": {
      "Cơ quan cấp": "",
      "Số văn bản": "",
      "Địa danh": "",
      "Ngày ký": "",
      "Người ký": "",
      "Chức danh": "",
      "Số vào sổ ĐKĐĐ": ""
    },
    "Người sử dụng đất": [
      {
        "Họ và tên": "",
        "Loại giấy tờ": "",
        "Số giấy tờ": "",
        "Ngày cấp": "",
        "Nơi cấp": "",
        "Địa chỉ thường trú": ""
      }
    ],
    "Thửa đất": {
      "Thửa đất số": "",
      "Tờ bản đồ số": "",
      "Địa chỉ": "",
      "Diện tích": "",
      "Sử dụng riêng": "",
      "Sử dụng chung": "",
      "Mục đích sử dụng": "",
      "Từ thời điểm": "",
      "Thời hạn đề nghị": "",
      "Nguồn gốc sử dụng": ""
    },
    "Xác nhận của UBND": {
      "Nguồn gốc kê khai": "",
      "Nguồn gốc sử dụng đất": "",
      "Thời điểm sử dụng đất vào mục đích": "",
      "Thời điểm tạo lập tài sản": "",
      "Tình trạng tranh chấp": "",
      "Sự phù hợp với quy hoạch": "",
      "Nội dung khác": ""
    },
    "Giấy tờ đã nộp": [],
    "Ghi chú": []
  }
}
"""

kqdk_extract_user_prompt = """Các ảnh này là MỘT giấy xác nhận đăng ký đất đai (có thể gồm nhiều trang liên tiếp của cùng văn bản). Bóc tách thành một object JSON duy nhất theo đúng cấu trúc đã cho. Chỉ trả JSON."""

kqdk_classify_system_prompt = """Bạn nhận MỘT ảnh là một trang trong bộ hồ sơ đất đai Việt Nam.
Trang thuộc chính văn bản đang xét gồm: tờ có tiêu đề "GIẤY XÁC NHẬN ĐĂNG KÝ ĐẤT ĐAI" (thường kèm tên Chi nhánh Văn phòng đăng ký đất đai ở góc trên trái và dòng "Số: …/GXN-…"); và các trang TIẾP THEO của chính văn bản đó (các mục 3, 4, 5, phần "Nơi nhận", chữ ký, dấu đỏ).

""" + _CLASSIFY_CHUNG

kqdk_classify_user_prompt = """Trang này thuộc chính giấy xác nhận đăng ký đất đai (kể cả trang tiếp theo, phần ký đóng dấu), hay là tài liệu đính kèm? Chỉ trả JSON {"role": "..."}."""


# ═════════════════════════════════════════════════════════════════════════════
# pcctt — PHIẾU THU THẬP THÔNG TIN ĐẤT ĐAI
# ═════════════════════════════════════════════════════════════════════════════

pcctt_extract_system_prompt = r"""Bạn là chuyên gia bóc tách hồ sơ đất đai Việt Nam. Ảnh dưới đây là MỘT PHIẾU THU THẬP THÔNG TIN ĐẤT ĐAI — biểu mẫu một trang, phần dữ liệu ĐƯỢC VIẾT TAY trên các dòng chấm.

""" + _CHUNG + r"""

Hướng dẫn từng mục (đánh số bám đúng phiếu):
1. Người sử dụng đất, chủ sở hữu tài sản gắn liền với đất, người quản lý đất:
   - 'Họ và tên người đại diện' (a).
   - 'Số CC/CCCD' (b) — dãy 9 hoặc 12 số, kèm 'Ngày cấp' và 'Nơi cấp' ở dòng dưới.
   - 'Địa chỉ' (c) — thường chỉ ghi tên thôn/xóm.
2. Thông tin thửa đất:
   - 'Thửa đất số' (a) và 'Tờ bản đồ số' (2.2). Số bị gạch bỏ thì lấy số ghi đè lên,
     chú thích "(cũ)" đi kèm thì giữ nguyên trong giá trị.
   - 'Địa chỉ' (b) — NỐI phần viết tay với phần in sẵn cuối dòng (vd viết tay "Thôn
     Đo (cũ)" + in sẵn "xã Trung Giã, thành phố Hà Nội").
   - 'Diện tích' (c), 'Mục đích sử dụng' (d).
   - 'Nguồn gốc sử dụng đất' (e) — khối nhiều dòng viết tay, CHÉP NGUYÊN VĂN toàn bộ,
     nối các dòng bằng dấu cách, không tóm tắt, không sửa chính tả, không thay bằng
     cụm pháp lý mẫu. Người dân thường viết kiểu "Do bà mẹ để lại…", "Các cụ để
     lại", "Nhận chuyển nhượng của ông X năm 2013", kèm các câu cam đoan "không có
     tranh chấp", "không vi phạm pháp luật đất đai", "không có giấy tờ về đất" —
     phải giữ đủ cả phần cam đoan đó.
3. 'Giấy tờ nộp kèm theo' — liệt kê từng dòng.
Cuối phiếu: 'Địa danh' + 'Ngày lập' (dạng "…, ngày … tháng … năm …", ngày có thể bỏ
trống → để ""), 'Người cung cấp thông tin' (tên ghi rõ dưới chữ ký).

Trả ĐÚNG cấu trúc JSON sau:

{
  "Phiếu thu thập": {
    "Người sử dụng đất": {
      "Họ và tên người đại diện": "",
      "Loại giấy tờ": "",
      "Số giấy tờ": "",
      "Ngày cấp": "",
      "Nơi cấp": "",
      "Địa chỉ": ""
    },
    "Thửa đất": {
      "Thửa đất số": "",
      "Tờ bản đồ số": "",
      "Địa chỉ": "",
      "Diện tích": "",
      "Mục đích sử dụng": "",
      "Nguồn gốc sử dụng": ""
    },
    "Giấy tờ nộp kèm": [],
    "Địa danh": "",
    "Ngày lập": "",
    "Người cung cấp thông tin": ""
  }
}
"""

pcctt_extract_user_prompt = """Ảnh là MỘT phiếu thu thập thông tin đất đai viết tay. Bóc tách thành một object JSON duy nhất theo đúng cấu trúc đã cho. Chỉ trả JSON."""

pcctt_classify_system_prompt = """Bạn nhận MỘT ảnh là một trang trong bộ hồ sơ đất đai Việt Nam.
Trang thuộc chính phiếu đang xét: tờ có tiêu đề "PHIẾU THU THẬP THÔNG TIN ĐẤT ĐAI" (thường kèm quốc hiệu phía trên, các mục "1. Người sử dụng đất…", "2. Thông tin thửa đất", "3. Những giấy tờ nộp kèm theo", điền tay trên dòng chấm), hoặc trang tiếp theo của chính phiếu đó.

""" + _CLASSIFY_CHUNG

pcctt_classify_user_prompt = """Trang này là chính phiếu thu thập thông tin đất đai, hay là tài liệu đính kèm? Chỉ trả JSON {"role": "..."}."""
