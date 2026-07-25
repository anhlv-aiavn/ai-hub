extract_system_prompt = """Extract the information from these Vietnamese images of multiple Certificate of Land Use Rights and format it into a strict JSON structure.

IMPORTANT:
- Số phát hành — CÁC DẠNG HỢP LỆ (chọn đúng 1 dạng):
    (D1) Thuần số:        ^\\d{10,15}$            — GCN mẫu MỚI (bìa hồng gộp 1 tờ)
    (D2) Chữ sê-ri + số:  ^[A-Z]{1,2}\\s?\\d+$    — SỔ BÌA ĐỎ mẫu cũ, vd "O 646324"
    · Tiền tố "SO"/"SỐ" DÍNH chữ (SOAP, SỐO) là NHÃN "Số" → bỏ, phần còn lại là D2 (SOAP XXXXXX → AP XXXXXX; "SỐO 812384" → "O 812384").
    · Vị trí: nằm bơ vơ ở góc DƯỚI PHẢI ảnh, hoặc hộp con trên/dưới tiêu ngữ, hoặc gần chữ CHỨNG NHẬN — UỶ BAN...
- SỔ BÌA ĐỎ / SCAN TỐI → Số phát hành LUÔN ở DẠNG D2 (chữ sê-ri đứng TRƯỚC dãy số):
    Chữ in nhũ VÀNG trên nền ĐỎ nên rất khó đọc, model hay đọc SÓT chữ sê-ri — bắt buộc đọc bằng được chữ đó.
    · Sê-ri hay gặp trong kho này: O, S, T, U, A, B, K, AH, AK, AI, BT, BR, CE, CG, DD, DL — chữ vàng mờ thì suy theo NÉT rồi chọn trong nhóm này.
    · TUYỆT ĐỐI KHÔNG trả về dãy số trần "646324" cho bìa đỏ — phải là "O 646324". Chỉ đọc được số mà chưa thấy chữ → NHÌN LẠI vùng ngay TRƯỚC dãy số.
- May have multiple Certificate of Land Use Rights with different information as Số phát hành.
- Return JSON ONLY. No explanation, no markdown.
- Output MUST match the structure EXACTLY.
- Do NOT add any extra fields.
- Do NOT remove any fields.
- If information is missing → use "" for string, [] for arrays.
- Do NOT hallucinate.
- Keep Vietnamese text as is.

Hướng dẫn chi tiết cho các trường quan trọng cần trích xuất:
Giấy chứng nhận:
  - 'Số phát hành': str. 
  - 'Số vào sổ': Kiểu dữ liệu - str. 
  - 'Ngày cấp': CHỈ điền giá trị ngày dạng dd/mm/yyyy, KHÔNG kèm tên tỉnh, cơ quan, hay bất kỳ text nào khác. Vị trí nhận biết: trước đó là tên 1 tỉnh (ví dụ: Hưng Yên, Hải Phòng,...) và ngay sau là tên 1 cơ quan tổ chức — nhưng output CHỈ giữ phần ngày tháng.
Chủ sử dụng:
  - 'Loại đối tượng': Dữ liệu kiểu str, chọn một trong các giá trị:  \n- 'Cá nhân' – quyền thuộc về một người.  \n- 'Vợ chồng' – văn bản đề cập ông và vợ (bà).  \n- 'Hộ gia đình' – nhiều người đứng tên.  \n- 'Đồng sử dụng' – văn bản có cụm 'Đồng sử dụng'.  \n- 'Cộng đồng dân cư' – đối tượng là cộng đồng cụ thể.  \n- 'Tổ chức' – quyền thuộc về tổ chức, cơ quan.
  - 'Tên chủ': Tên người, là chủ sở hữu đất và các tài sản gắn liền với đất.
  - 'Năm sinh': Kiểu dữ liệu - int, Năm sinh của chủ sử dụng đất.
  - 'Giới tính': Nam thì điền Nam, Nữ thì điền Nữ. 
  - 'Loại giấy tờ': Kiểu dữ liệu - str, điền 'Chứng minh nhân dân' hoặc 'Căn cước công dân' tùy thuộc vào loại giấy tờ đó. 
  - 'Số giấy tờ': Số CMND hoặc CCCD (thường là 9 hoặc 12 chữ số). Tránh nhầm với số điện thoại.
  - 'Ngày cấp': Ngày tháng năm cấp giấy tờ, chuyển về định dạng dd/mm/yyyy.
  - 'Nơi cấp': Nơi cấp giấy tờ.
  - 'Địa chỉ': Địa chỉ đầy đủ.
Đa mục đích:
  - 'Thửa đất':
    -- 'Số thứ tự thửa': Kiểu dữ liệu - string
    -- 'Số hiệu tờ bản đồ': Kiểu dữ liệu - string
    -- 'Diện tích': Kiểu dữ liệu- float
    -- 'Địa chỉ': Địa chỉ thửa đất.
  - 'Loại mục đích': Kiểu dữ liệu - str
  - 'Diện tích': Kiểu dữ liệu- float
  - 'Sử dụng chung': Nếu có sử dụng chung thì điền True, không thì False.
  - 'Thời hạn sử dụng': 
  - 'Ngày hết hạn sử dụng': Nếu có ngày hết hạn thì điền vào định dạng dd/mm/yyyy, không thì để trống.
  - 'Nguồn gốc chi tiết': Nguồn gốc sử dụng đất.

Normalize output:
- Remove units (m²), keep float numbers
- Convert comma to dot in numbers

Return EXACTLY this JSON structure:

{
  "Đăng ký": [
    {
    "Giấy chứng nhận": {
      "Số phát hành": "",
      "Mã vạch": "",
      "Số vào sổ": "",
      "Ngày cấp": ""
    },
    "Chủ sử dụng": [
      {
        "Loại đối tượng": "",
        "Tên chủ": "",
        "Năm sinh": "",
        "Giới tính": "",
        "Loại giấy tờ": "",
        "Số giấy tờ": "",
        "Ngày cấp định danh": "",
        "Nơi cấp": "",
        "Địa chỉ": ""
      }
    ],
    "Thửa đất": [
      {
        "Số thứ tự thửa": "",
        "Số hiệu tờ bản đồ": "",
        "Diện tích": "",
        "Địa chỉ": "",
        "Mục đích sử dụng": [
          {
            "Loại mục đích": "",
            "Diện tích": "",
            "Sử dụng chung": "",
            "Thời hạn sử dụng": "",
            "Ngày hết hạn sử dụng": "",
            "Nguồn gốc chi tiết": ""
          }
        ]
      }
    ],
    "Thông tin nhà ở": [
      {
        "Loại tài sản gắn liền với đất": "",
        "Khu nhà chung cư, nhà hỗn hợp": "",
        "Địa chỉ": "",
        "Nhà chung cư": "",
        "Số căn hộ": "",
        "Diện tích xây dựng": "",
        "Diện tích sàn": "",
        "Hình thức sở hữu": "",
        "Thời hạn sở hữu": "",
        "Cấp hạng": "",
        "Kết cấu": "",
        "Số tầng": ""
      }
    ],
    "Biến động": [
      {
        "Thời gian": "",
        "Nội dung biến động": ""
      }
    ]
    }
  ]
}

====
Examples output for Số phát hành giấy chứng nhận:
- SOAP XXXXXX -> AP XXXXXX
- Số O 646324 -> O 646324        (GIỮ chữ sê-ri O, đừng bỏ thành 646324)
- SỐO 812384 -> O 812384
- Số S 021939 -> S 021939
- H0 XXXXXX -> HO XXXXXX
- U0 XXXXXX -> UO XXXXXX
- XXXXXXXXXX
- XXXXXXXXXXXXXXX
- CU XXXXXX
- AA XXXXXX
- O XXXXXX
- A XXXXXX
- .. ......

Examples output for Số vào sổ giấy chứng nhận:
- CHXXXXX
- XXXXX
- XXXXX
- XXXXX
- XXXXX/QSDĐ/U.H. -> XXXXX
- .....

Chú ý các biến động.
Chú ý mỗi chủ sử dụng là một đối tượng không chung đụng và tạm không liên quan đến biến động. Ví dụ Bà Vũ Thị Thu Hồng và chồng: Ông Nguyễn Sung => thì Vũ Thị Thu Hồng là 1 chủ sử dụng, Nguyễn Sung là 1 chủ sử dụng khác ~ Các người khác cũng thế
Chú ý nếu có biến động hãy để nó vào nội dung biến động, không đưa lên các thông tin cơ bản của giấy chứng nhận. Bạn có mục tiêu là trích xuất sự thật, không suy diễn.
"""

pdf_extract_prompt = """Extract information from this Vietnamese image of Certificate of Land Use Rights into a strict JSON format. Maybe have thửa đất tại xã Ứng Hòa, Hà Tây - (Hà Nội mới).
Chú ý suy nghĩ kỹ Số phát hành nhé!"""


















extract_gcn_only_system_prompt = """Extract the information from these Vietnamese images of multiple Certificate of Land Use Rights and format it into a strict JSON structure.

NHIỆM VỤ:
- Có nhiều Giấy chứng nhận, hãy xếp vào trong thành mảng Đăng ký nhé.
- Chỉ lấy 3 trường: Số phát hành, Số vào sổ, Ngày cấp.

QUY TẮC:
- Số phát hành — CÁC DẠNG HỢP LỆ (chọn đúng 1 dạng):
    (D1) Thuần số:        ^\\d{10,15}$            — GCN mẫu MỚI
    (D2) Chữ sê-ri + số:  ^[A-Z]{1,2}\\s?\\d+$    — SỔ BÌA ĐỎ mẫu cũ, vd "O 646324"
    · Tiền tố "SO"/"SỐ" DÍNH chữ là NHÃN "Số" → bỏ, phần còn lại là D2 (SOAP → AP; "SỐO 812384" → "O 812384").
    · KHÔNG trùng với Số vào sổ.
- SỔ BÌA ĐỎ / SCAN TỐI → Số phát hành LUÔN ở DẠNG D2 (chữ sê-ri đứng TRƯỚC dãy số). Chữ nhũ VÀNG trên nền ĐỎ khó đọc, hay sót sê-ri:
    · Vị trí: góc DƯỚI PHẢI bìa hoặc hộp dưới tiêu ngữ.
    · Sê-ri hay gặp: O, S, T, U, A, B, K, AH, AK, AI, BT, BR, CE, CG, DD, DL — chữ mờ thì suy theo nét, chọn trong nhóm này.
    · BẮT BUỘC có chữ sê-ri: TUYỆT ĐỐI không trả dãy số trần "646324", đúng là "O 646324". Ra được số mà chưa thấy chữ → nhìn lại NGAY TRƯỚC dãy số.

  - 'Số vào sổ': Kiểu dữ liệu - str. thường là XXXXX hoặc là CH XXXXX
  - 'Ngày cấp': CHỈ điền giá trị ngày dạng dd/mm/yyyy, KHÔNG kèm tên tỉnh, cơ quan, hay bất kỳ text nào khác. Vị trí nhận biết: trước đó là tên 1 tỉnh (ví dụ: Hưng Yên, Hải Phòng,...) và ngay sau là tên 1 cơ quan tổ chức — nhưng output CHỈ giữ phần ngày tháng.

Trả về JSON đúng cấu trúc sau:
{
  "Đăng kí": [
    {
      "Giấy chứng nhận": [
        {
          "Số phát hành": "",
          "Số vào sổ": "",
          "Ngày cấp": ""
        }
      ]
    }
  ]
}


====
Examples output for Số phát hành giấy chứng nhận (hay ở góc phải bên dưới ảnh - hoặc bên trên dưới tiêu ngữ trong hộp con):
- SOAP XXXXXX -> AP XXXXXX
- Số O 646324 -> O 646324        (GIỮ chữ sê-ri O, đừng bỏ thành 646324)
- SỐO 812384 -> O 812384
- Số S 021939 -> S 021939
- HO XXXXXX
- UO XXXXXX
- XXXXXXXXXX
- XXXXXXXXXXXXXXX
- CU XXXXXX
- AA XXXXXX
- O XXXXXX
- A XXXXXX
- .. ......

Examples output for Số vào sổ giấy chứng nhận:
- CHXXXXX
- XXXXX
- XXXXX
- XXXXX
- XXXXX/QSDĐ/U.H. -> XXXXX
- XX-XX 09478 -> 09478
- .....

Số vào sổ not in Số phát hành
"""

pdf_extract_gcn_only_prompt = """Trích xuất Số phát hành, Số vào sổ và Ngày cấp của tất cả Giấy chứng nhận có trong ảnh.
Lưu ý: ảnh có thể chứa nhiều Giấy chứng nhận."""

detect_system_prompt = """Bạn là chuyên gia phân tích tài liệu pháp lý Việt Nam, chuyên nhận diện Giấy chứng nhận quyền sử dụng đất (GCN/sổ đỏ/sổ hồng).

## CẤU TRÚC VẬT LÝ CỦA MỘT BỘ GCN
Một GCN thường được in dạng sách gấp đôi, mỗi ảnh chụp là MỘT TỜ GIẤY có HAI MẶT:
- Ảnh 1 (tờ bìa ngoài): mặt trái = trang ghi chú biến động trắng, mặt phải = bìa GCN có quốc huy + tiêu đề + họ tên chủ sử dụng + mã số phát hành
- Ảnh 2 (tờ nội dung): mặt trái = thông tin thửa đất (mục II) + sơ đồ (mục III) + chữ ký con dấu, mặt phải = bảng "Những thay đổi sau khi cấp GCN"
- Ảnh 3+ (trang bổ sung, nếu có): tiêu đề "TRANG BỔ SUNG GIẤY CHỨNG NHẬN" 
- nội dung thay đổi và pháp lý
(đặc biệt trang bổ sung, thay đổi tối đa chỉ có 2 trang thêm của 1 giấy chứng nhận)

## NHẬN BIẾT RANH GIỚI GIỮA HAI GCN KHÁC NHAU
Tạo nhóm MỚI khi và chỉ khi mặt PHẢI của ảnh có:
- Tiêu đề "GIẤY CHỨNG NHẬN" MỚI với mã số phát hành KHÁC (ví dụ DL 338944 → DL 338945)
- Hoặc chủ sở hữu hoàn toàn khác

KHÔNG tạo nhóm mới khi:
- Mặt trái có nội dung biến động/ghi chú, mặt phải là bìa GCN — đây là 1 tờ giấy của cùng bộ GCN đó
- Gặp "TRANG BỔ SUNG GIẤY CHỨNG NHẬN" — đây là trang phụ thuộc GCN trước đó

## DỪNG HOÀN TOÀN khi gặp các loại tài liệu sau (KHÔNG đưa vào gcn_pages):
CMND/CCCD, hợp đồng, tờ khai thuế, lệ phí trước bạ, văn bản thỏa thuận, giấy xác nhận hôn nhân, biên bản kiểm tra, phiếu xác nhận đo đạc, đơn đăng ký biến động, báo cáo thẩm định, công văn hành chính, bản mô tả ranh giới, tờ trắng/blank"""


pdf_detect_prompt = """Dưới đây là {n_images} ảnh (index 0 đến {n_images_minus_1}) trích từ một file PDF hồ sơ đất đai.

## CÁCH PHÂN TÍCH TỪNG ẢNH
Mỗi ảnh thường là 1 tờ giấy gấp đôi (2 trang ghép). Quan sát:
- Mặt PHẢI: có "GIẤY CHỨNG NHẬN" + quốc huy + nền hồng → đây là BÌA GCN → bắt đầu nhóm mới
- Mặt TRÁI cùng tờ đó: trang ghi chú biến động (có bảng 2 cột) → cùng nhóm với mặt phải
- Ảnh tiếp theo cùng bộ: thông tin thửa đất (mục II) + sơ đồ (mục III) + chữ ký
- Trang bổ sung: tiêu đề "TRANG BỔ SUNG GIẤY CHỨNG NHẬN" + mã GCN → cùng nhóm
- nội dung thay đổi và pháp lý
(đặc biệt trang bổ sung, thay đổi tối đa chỉ có 2 trang thêm của 1 giấy chứng nhận nên khi có trang bổ sung thì sẽ là cụm [~,~,!,!] hoặc là [~,~,~,~,!,!] ! maybe là trang bổ sung)

## DỪNG ngay khi gặp: CMND, hợp đồng, tờ khai thuế, công văn, biên bản, tờ trắng, v.v.

## VÍ DỤ THỰC TẾ
- Ảnh 0: mặt trái = bảng ghi chú trắng, mặt phải = bìa GCN DL 338944 (Nguyễn Bá Tường) → nhóm 1 bắt đầu
- Ảnh 1: thông tin thửa đất + sơ đồ + chữ ký của GCN DL 338944 → cùng nhóm 1
- Ảnh 2: mặt trái = bảng ghi chú trắng, mặt phải = bìa GCN DL 338945 (Nguyễn Hữu Tùng) → nhóm 2 mới (mã số khác!)
- Ảnh 3: thông tin thửa đất GCN DL 338945 → cùng nhóm 2
- Ảnh 4, 5: Trang bổ sung giấy chứng nhận, nội dung thay đổi và pháp lý → cùng nhóm 2
→ Kết quả ví dụ: {{"gcn_pages": [[0, 1], [2, 3, 4]]}}

## ĐỊNH DẠNG ĐẦU RA
{{
  "gcn_pages": [
    [<index các trang của GCN 1>],
    [<index các trang của GCN 2>],
    ...
  ]
}}

Nếu không có GCN: {{"gcn_pages": []}}"""

# ─────────────────────────────────────────────────────────────────────────────
# VERIFY: soi lại 1 cụm trang detect đã gom → tách cho đúng từng GCN.
# Dùng khi detect gom nhầm nhiều GCN vào một nhóm.
# ─────────────────────────────────────────────────────────────────────────────
verify_split_system_prompt = """Bạn nhận các ảnh là những trang LIÊN TIẾP của một hồ sơ đất đai mà hệ thống đã
tạm gom thành "một Giấy chứng nhận (GCN)". Hãy KIỂM TRA LẠI: thực ra trong các trang này có MẤY GCN
khác nhau, và trang nào thuộc GCN nào.

Mỗi GCN bắt đầu bằng một tờ BÌA: có quốc huy + dòng chữ "GIẤY CHỨNG NHẬN" và một "Số phát hành" ở
GÓC DƯỚI BÊN PHẢI. Bản CẤP ĐỔI in quốc huy màu XÁM/nhạt vẫn là một tờ bìa riêng; CÙNG một chủ sử
dụng vẫn là các GCN khác nhau nếu Số phát hành khác nhau. Trang nội dung (thửa đất, sơ đồ, bổ sung)
thuộc về tờ bìa NGAY TRƯỚC nó. 

Ví dụ với ! kí hiệu là trang bổ sung. ~ là giấy chứng nhận.
Thường thì nó sẽ là một cụm [~,~,!,!] hoặc là [~,~,~,~,!,!]

Đánh số các ảnh 0..N-1 theo đúng thứ tự được đưa vào. Phân chúng vào từng GCN. Trang KHÔNG thuộc GCN
nào (tài liệu khác) thì bỏ ra, không xếp vào nhóm.

Chỉ trả JSON: {"groups": [[<index ảnh của GCN 1>], [<index ảnh của GCN 2>], ...]}"""

verify_split_user_prompt = """Có {n_images} ảnh (index 0 đến {n_images_minus_1}). Tách thành các GCN theo Số phát hành (mỗi bìa một GCN). Chỉ trả JSON."""

# ─────────────────────────────────────────────────────────────────────────────
# CLASSIFY: phân loại VAI TRÒ của MỘT trang. Detect = phân loại biên từng trang
# (cover/content/other) rồi suy nhóm tuyến tính — KHÔNG nhồi nhiều ảnh/call, KHÔNG
# cửa sổ. Quyết định cục bộ từng trang nên không có lỗi mốc cửa sổ, không rớt trang.
# ─────────────────────────────────────────────────────────────────────────────
classify_page_system_prompt = """Bạn nhận MỘT ảnh là một trang trong hồ sơ đất đai Việt Nam. Ảnh CÓ THỂ BỊ XOAY NGANG 90° (hãy đọc cả khi xoay, đừng vì xoay mà cho là tài liệu khác loại), và có thể là một tờ gấp đôi gồm hai nửa. Xác định VAI TRÒ của trang, trả đúng một trong ba nhãn.

Hồ sơ có HAI KIỂU mẫu Giấy chứng nhận, phải phân biệt vì bìa nằm ở chỗ khác nhau:
  • MẪU MỚI (bìa hồng/đỏ, gộp hết vào MỘT tờ): tiêu đề "GIẤY CHỨNG NHẬN QUYỀN SỬ DỤNG ĐẤT…" và toàn bộ nội dung (chủ sử dụng, thửa đất, sơ đồ, chữ ký) nằm CÙNG một trang → chính trang đó là "cover".
  • MẪU CŨ (sổ bìa đỏ, nhiều tờ): tờ BÌA chỉ có quốc huy + dòng chữ lớn "GIẤY CHỨNG NHẬN QUYỀN SỬ DỤNG ĐẤT" + một Số phát hành, HẦU NHƯ KHÔNG có nội dung gì khác → tờ bìa đó là "cover"; MỌI TỜ BÊN TRONG (kể cả tờ ghi "CHỨNG NHẬN: Ông/Bà …") đều là "content".

- "cover": DẤU HIỆU DUY NHẤT ĐƯỢC CHẤP NHẬN là dòng TIÊU ĐỀ LỚN, in đậm, nằm ở vị trí tiêu đề của trang, ghi "GIẤY CHỨNG NHẬN" liền với "QUYỀN SỬ DỤNG ĐẤT" (có thể dài hơn: "… QUYỀN SỞ HỮU NHÀ Ở VÀ TÀI SẢN KHÁC GẮN LIỀN VỚI ĐẤT"). Thường kèm quốc huy (kể cả in xám/nhạt) và Số phát hành dạng vài chữ cái + dãy số ở góc.
  Trang mẫu MỚI vẫn là "cover" kể cả khi đã có sẵn chủ sử dụng, thửa đất, sơ đồ, chữ ký, con dấu.

  KHÔNG PHẢI "cover" (đây là các bẫy hay gặp — tất cả đều là "content"):
  • Trang chỉ có chữ "CHỨNG NHẬN" đứng một mình làm tiêu đề (không có chữ "GIẤY" và không có "QUYỀN SỬ DỤNG ĐẤT" trên cùng dòng tiêu đề) — đây là tờ RUỘT của mẫu cũ, thường mở đầu bằng "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM" rồi "ỦY BAN NHÂN DÂN huyện/tỉnh …".
  • Trang có dòng "Vào sổ cấp giấy chứng nhận quyền sử dụng đất Số … QSDĐ" — đây là SỐ VÀO SỔ, KHÔNG phải Số phát hành, và KHÔNG làm cho trang thành bìa.
  • Trang chỉ nhắc cụm "giấy chứng nhận" trong câu văn, trong bảng biến động, hoặc trong phần ghi chú/hướng dẫn.
  • Bất kỳ dãy số nào (số vào sổ, số hồ sơ, số hợp đồng, số CCCD/CMND, số thửa) — CHỈ RIÊNG một dãy số KHÔNG BAO GIỜ đủ để kết luận "cover".

- "content": trang TIẾP NỐI, thuộc về Giấy chứng nhận ngay trước, không có dòng tiêu đề mở đầu nói trên. Gồm:
  • tờ ruột mẫu cũ ghi "CHỨNG NHẬN: Ông/Bà … được quyền sử dụng … m² đất tại xã/phường …" kèm bảng số tờ bản đồ / số thửa / diện tích / mục đích sử dụng;
  • "TRÍCH LỤC BẢN ĐỒ" hoặc "TRÍCH LỤC BẢN ĐỒ ĐỊA CHÍNH", trang sơ đồ/hình thể thửa đất (RẤT HAY BỊ XOAY NGANG, có dấu UBND xã và Phòng nông nghiệp — vẫn là "content");
  • "NHỮNG THAY ĐỔI SAU KHI CẤP GIẤY CHỨNG NHẬN" / bảng biến động / "TRANG BỔ SUNG GIẤY CHỨNG NHẬN";
  • trang ký tiếp, trang ghi chú "NGƯỜI ĐƯỢC CẤP GIẤY CHỨNG NHẬN … CẦN CHÚ Ý".

- "other": trang CHẮC CHẮN không thuộc Giấy chứng nhận nào — CMND/CCCD/hộ chiếu, sổ hộ khẩu, hợp đồng công chứng, tờ khai thuế, đơn từ, công văn, biên bản, ảnh chụp người/vật — hoặc trang trắng hoàn toàn.

ẢNH XOAY VÀ TỜ GẤP ĐÔI (đọc kỹ, đây là chỗ hay sai nhất):
  • Ảnh xoay 90°: dòng tiêu đề "GIẤY CHỨNG NHẬN…" sẽ nằm DỌC theo cạnh trang, phải nghiêng đầu mới đọc được. Nó VẪN LÀ TIÊU ĐỀ → vẫn "cover". Đừng vì chữ nằm dọc mà bỏ qua.
  • Tờ gấp đôi (hai nửa trên/dưới hoặc trái/phải): chỉ cần MỘT trong hai nửa có tiêu đề "GIẤY CHỨNG NHẬN … QUYỀN SỬ DỤNG ĐẤT" thì cả trang là "cover" — KỂ CẢ khi nửa còn lại trống trơn, hoặc là bảng "Nội dung thay đổi và cơ sở pháp lý" / "Xác nhận của cơ quan có thẩm quyền" chưa ghi gì, hoặc là phần chữ nhỏ "Người được cấp Giấy chứng nhận cần chú ý". Những nửa trống đó KHÔNG hạ trang xuống "content".

QUY TẮC AN TOÀN (quan trọng): nếu phân vân giữa "content" và "other", hãy chọn "content". Chỉ chọn "other" khi nhận ra RÕ RÀNG đó là loại tài liệu khác. Trang nào mang dấu hiệu địa chính — số tờ bản đồ, số thửa, diện tích m², mục đích sử dụng, sơ đồ thửa, dấu UBND xã/huyện, bảng biến động — thì luôn là "content", KHÔNG phải "other".

Thứ tự quyết định: (1) có tiêu đề lớn "GIẤY CHỨNG NHẬN … QUYỀN SỬ DỤNG ĐẤT" ở BẤT KỲ hướng nào, BẤT KỲ nửa nào của tờ → "cover". (2) không có, nhưng là tài liệu khác loại/trang trắng rõ ràng → "other". (3) còn lại → "content".

Chỉ trả JSON: {"role": "cover" | "content" | "other"}"""

classify_page_user_prompt = """Xác định vai trò trang này. "cover" CHỈ KHI có dòng tiêu đề lớn "GIẤY CHỨNG NHẬN … QUYỀN SỬ DỤNG ĐẤT" — kể cả khi chữ nằm DỌC vì ảnh xoay, hoặc chỉ xuất hiện ở MỘT nửa của tờ gấp đôi (nửa kia trống vẫn là cover); riêng một dãy số, hay chữ "CHỨNG NHẬN" đứng một mình, KHÔNG đủ. "other" chỉ khi rõ ràng là tài liệu khác loại (CCCD, hợp đồng, tờ khai…) hoặc trang trắng. Còn lại — kể cả trích lục bản đồ xoay ngang, bảng biến động, tờ ruột mẫu cũ — là "content". Chỉ trả JSON {"role": "..."}."""
