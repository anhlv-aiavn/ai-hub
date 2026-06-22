import asyncio
import io

from src.extentions.multimodal.make import pdf_to_corrected_images
from src.extentions.multimodal.prompt import (
    detect_system_prompt,
    pdf_detect_prompt,
    verify_split_system_prompt,
    verify_split_user_prompt,
)
from src.extentions.multimodal.vlm_client import chat_json


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
