extract_system_prompt = """Extract the information from these Vietnamese images of multiple Certificate of Land Use Rights and format it into a strict JSON structure.

IMPORTANT:
- MUST HAVE Số phát hành Một trong 3 dạng:
    1. ^\\d{10,15}$
    2. ^[A-Z]{1,2}\\s?\\d+$
    3. Tiền tố "SO" + dạng 2 → bỏ "SO"
- May have multiple Certificate of Land Use Rights with different information as Số phát hành. (chú ý) Và Giấy loại cũ sẽ có biến động và đưa ra loại các giấy mới khác đồng thời kiểu số phát hành cũng có thể khác.
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
  - 'Giới tính': Nam thì điền True, Nữ thì điền False. 
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
"""

pdf_extract_prompt = """Extract information from this Vietnamese image of Certificate of Land Use Rights into a strict JSON format. Maybe have thửa đất tại xã Ứng Hòa, Hà Tây - (Hà Nội mới).
Chú ý suy nghĩ kỹ Số phát hành nhé!"""


















extract_gcn_only_system_prompt = """Extract the information from these Vietnamese images of multiple Certificate of Land Use Rights and format it into a strict JSON structure.

NHIỆM VỤ:
- Có nhiều Giấy chứng nhận, hãy xếp vào trong thành mảng Đăng ký nhé.
- Chỉ lấy 3 trường: Số phát hành, Số vào sổ, Ngày cấp.

QUY TẮC:
- Số phát hành — một trong 3 dạng:
    1. ^\\d{10,15}$
    2. ^[A-Z]{1,2}\\s?\\d+$
    3. Tiền tố "SO" + dạng 2 → bỏ "SO"
    4. Không trùng với số vào sổ

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