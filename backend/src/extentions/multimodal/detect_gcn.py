import asyncio
import io

from src.extentions.multimodal.make import pdf_to_corrected_images
from src.extentions.multimodal.prompt import (
    classify_page_system_prompt,
    classify_page_user_prompt,
    detect_system_prompt,
    pdf_detect_prompt,
    verify_split_system_prompt,
    verify_split_user_prompt,
)
from src.extentions.multimodal.vlm_client import chat_json

_ROLES = ("cover", "content", "other")


def groups_from_roles(roles: list[str]) -> list[list[int]]:
    """Suy nhóm GCN tuyến tính từ nhãn vai trò từng trang (cover/content/other).

    NGUỒN DUY NHẤT của luật gom nhóm — worker và mọi script audit/bench đều gọi
    hàm này. Trước đây logic bị chép ra 3 nơi; sửa lệch nhau thì công cụ đo sẽ
    báo sai về chính production.

    - "cover"  → mở một nhóm MỚI (GCN được ĐỊNH DANH bởi bìa).
    - "content"→ nối vào nhóm đang mở.
    - "other"  → loại trang đó + đóng nhóm hiện tại.

    LUẬT MỘT BÌA (quan trọng nhất, chặn trên mọi luật khác): nếu cả file CHỈ CÓ
    ĐÚNG MỘT trang bìa thì chắc chắn chỉ có MỘT giấy → gom TẤT CẢ trang cover +
    content vào một nhóm, BẤT KỂ thứ tự. Đo trên dữ liệu thật: hồ sơ được scan
    với thứ tự lộn tung là chuyện thường (BP 680351 bìa ở tr.4, AN 077157 bìa ở
    tr.6, 10103092758 nội dung nằm rải tr.4 và tr.6), trong khi NHÃN BÌA lại gần
    như luôn đúng. Suy nhóm theo vị trí tuyến tính vì thế vứt oan hàng loạt trang.

    Đánh đổi có chủ ý: thà GOM DƯ một trang phụ (extract chỉ tốn thêm chút ngữ
    cảnh) còn hơn VỨT một trang biến động — vứt là mất dữ liệu, im lặng, và làm
    chủ cuối sai mà không hề có cảnh báo.

    XẾP NGƯỢC (khi có ≥2 bìa, luật một bìa không áp dụng): những trang "content"
    đứng trước bìa ĐẦU TIÊN được giữ lại và gắn vào nhóm của bìa đó thay vì vứt.

    Vẫn giữ: "content" lạc mà TRONG CẢ FILE không có bìa nào → bỏ, không chế ra
    GCN giả từ trang phụ trợ.
    """
    if sum(1 for r in roles if r == "cover") == 1:
        return [[i for i, r in enumerate(roles) if r in ("cover", "content")]]

    groups: list[list[int]] = []
    cur: list[int] | None = None
    dau: list[int] = []   # content đứng trước bìa đầu tiên — giữ tạm, chờ bìa
    da_co_bia = False
    for i, role in enumerate(roles):
        if role == "cover":
            cur = [i]
            groups.append(cur)
            if not da_co_bia and dau:
                cur[:0] = dau   # gắn phần đầu vào nhóm của bìa ĐẦU TIÊN
                dau = []
            da_co_bia = True
        elif role == "content":
            if cur is not None:
                cur.append(i)
            elif not da_co_bia:
                dau.append(i)   # chưa thấy bìa nào → chờ, chưa vứt
        else:  # "other" → loại trang, đóng nhóm
            cur = None
    return [g for g in groups if g]


async def classify_page(image_b64: str, enable_thinking: bool | None = None) -> str:
    """Phân loại VAI TRÒ một trang: "cover" (bìa GCN → mở giấy mới), "content"
    (nội dung thuộc bìa trước đó) hay "other" (không thuộc GCN nào).

    Đây là đơn vị của detect kiểu phân loại-biên-từng-trang: mỗi call chỉ 1 ảnh
    nên VLM chính xác cao + batch tốt; suy nhóm tuyến tính ở tầng trên.

    Lỗi/định dạng lạ → trả "content" (fail-safe: giữ trang, không rớt, không cắt
    nhầm thành giấy mới).
    """
    if not image_b64:
        return "content"
    d = await chat_json(
        system_prompt=classify_page_system_prompt,
        user_text=classify_page_user_prompt,
        images_b64=[image_b64],
        enable_thinking=enable_thinking,
    )
    role = (d.get("role") or "").strip().lower() if isinstance(d, dict) else ""
    return role if role in _ROLES else "content"


async def verify_split(
    images_b64: list[str], enable_thinking: bool | None = None
) -> list[list[int]]:
    """Soi lại 1 cụm trang đã gom → tách thành các GCN đúng (index CỤC BỘ 0..N-1).

    Dùng khi nghi detect gom nhầm nhiều GCN vào một nhóm. Trả list nhóm con; nếu model
    không tách được thì trả nguyên cụm là 1 nhóm.

    enable_thinking: None → theo env ENABLE_THINKING; True/False → ép bật/tắt.
    """
    n = len(images_b64)
    if n == 0:
        return []
    d = await chat_json(
        system_prompt=verify_split_system_prompt,
        user_text=verify_split_user_prompt.format(n_images=n, n_images_minus_1=n - 1),
        images_b64=images_b64,
        enable_thinking=enable_thinking,
    )
    out: list[list[int]] = []
    for g in d.get("groups") or []:
        gg = sorted({j for j in g if isinstance(j, int) and 0 <= j < n})
        if gg:
            out.append(gg)
    return out or [list(range(n))]


async def detect(images_b64: list[str], enable_thinking: bool | None = None) -> dict:
    """Nhóm trang GCN từ list ảnh đã render. Sampling tất định (chat_json mặc định
    temperature=0).

    enable_thinking: None → theo env ENABLE_THINKING; True/False → ép bật/tắt.

    Returns: {"gcn_pages": [[idx,...], [idx,...]]}  — mỗi phần tử là một nhóm/GCN.
    """
    if not images_b64:
        return {"gcn_pages": []}

    user_text = pdf_detect_prompt.format(
        n_images=len(images_b64),
        n_images_minus_1=len(images_b64) - 1,
    )
    return await chat_json(
        system_prompt=detect_system_prompt,
        user_text=user_text,
        images_b64=images_b64,
        enable_thinking=enable_thinking,
    )


async def _process_pdf(file_path: str):
    with open(file_path, "rb") as f:
        pdf_bytesio = io.BytesIO(f.read())
    loop = asyncio.get_running_loop()
    images = await loop.run_in_executor(None, pdf_to_corrected_images, pdf_bytesio)
    result = await detect(images)
    print(f"\n{'=' * 30}\n{file_path}\n{result}")
    return result


async def main():
    file_paths = [
        "/home/vpdk02/nlp/bags/extract_gcn/tests/output/0_10363-GCN-BO 175400.pdf",
        "/home/vpdk02/nlp/bags/extract_gcn/tests/output/0_10363-GCN-BO 175403.pdf",
        "/home/vpdk02/nlp/bags/extract_gcn/tests/output/0_BO 175399-GCN.pdf",
        "/home/vpdk02/nlp/bags/extract_gcn/tests/output/0_BN 953446-GCN.pdf",
        "/home/vpdk02/nlp/bags/extract_gcn/tests/output/0_A 934013-GCN.pdf",
        "/home/vpdk02/nlp/bags/extract_gcn/tests/output/0_AA 00827763-GCN.pdf",
    ]
    return await asyncio.gather(*(_process_pdf(p) for p in file_paths))


if __name__ == "__main__":
    asyncio.run(main())
