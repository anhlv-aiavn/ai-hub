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

detect_system_prompt = """Bạn là chuyên gia nhận diện Giấy chứng nhận quyền sử dụng đất (GCN) Việt Nam.

Nhận một dãy ảnh (mỗi ảnh = 1 trang). Hãy NHÓM các trang theo từng GCN.

Nhận diện tờ BÌA của một GCN: có quốc huy (hình tròn, ngôi sao vàng) + dòng chữ lớn
"GIẤY CHỨNG NHẬN", và một "Số phát hành" ở GÓC DƯỚI BÊN PHẢI (thường là 1-2 chữ cái + dãy số,
hoặc một dãy 10-15 chữ số). Bản CẤP ĐỔI in quốc huy màu XÁM/nhạt vẫn là tờ bìa.

Quy tắc nhóm:
- Mỗi tờ BÌA bắt đầu MỘT GCN (một nhóm) mới. Các trang nội dung đi NGAY SAU bìa (Mục II Thửa
  đất, Mục III Sơ đồ, bảng tọa độ, trang bổ sung) thuộc về GCN của bìa đó.
- Mỗi Số phát hành khác nhau là một GCN khác nhau — CÙNG một chủ sử dụng vẫn phải TÁCH.
  Số nhóm = số tờ bìa.

KHÔNG đưa vào kết quả các trang KHÔNG phải GCN: CMND/CCCD, hợp đồng, tờ khai thuế, lệ phí trước
bạ, văn bản thỏa thuận, phiếu kiểm soát/giải quyết hồ sơ, đơn đăng ký biến động, biên bản,
báo cáo thẩm định, công văn hành chính, bản mô tả ranh giới, tờ trắng/blank.

Trả về JSON, KHÔNG giải thích. Mỗi phần tử của "gcn_pages" là MỘT NHÓM (list index trang của
một GCN):
{"gcn_pages": [[<index nhóm 1>], [<index nhóm 2>], ...]}
Nếu không có GCN: {"gcn_pages": []}"""


pdf_detect_prompt = """Dưới đây là {n_images} ảnh (index 0 đến {n_images_minus_1}) từ một hồ sơ đất đai.
Nhóm các trang theo từng GCN: nhận diện tờ BÌA qua quốc huy + "GIẤY CHỨNG NHẬN" + Số phát hành ở
góc dưới bên phải (bìa cấp đổi quốc huy XÁM/nhạt vẫn tính; cùng một chủ vẫn tách theo Số phát hành);
loại bỏ các trang không phải GCN.
Chỉ trả JSON: {{"gcn_pages": [[...], [...]]}}. Nếu không có GCN: {{"gcn_pages": []}}"""