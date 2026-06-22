import asyncio
import io

from src.extentions.multimodal.make import pdf_to_corrected_images
from src.extentions.multimodal.prompt import detect_system_prompt, pdf_detect_prompt
from src.extentions.multimodal.vlm_client import chat_json


def groups_from_detect(
    gcn_pages: list[int], start_pages: list[int], n: int,
) -> list[list[int]]:
    """Gom nhóm GCN bằng CODE từ output detect (gcn_pages + start_pages).

    - gcn_pages: mọi trang thuộc GCN (đã loại trang rác).
    - start_pages: trang BẮT ĐẦU mỗi GCN (tờ bìa).
    Mỗi start mở 1 nhóm, ôm các trang gcn_pages từ start đó tới ngay trước start kế.
    Trang gcn_pages nằm trước start đầu tiên → gắn vào nhóm đầu.
    """
    gset = sorted({p for p in gcn_pages if isinstance(p, int) and 0 <= p < n})
    if not gset:
        return []
    starts = sorted({p for p in start_pages if isinstance(p, int) and 0 <= p < n})
    if not starts:
        return [gset]
    groups: list[list[int]] = []
    for i, s in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else n
        grp = [p for p in gset if s <= p < end]
        if grp:
            groups.append(grp)
    head = [p for p in gset if p < starts[0]]
    if head:
        if groups:
            groups[0] = sorted(head + groups[0])
        else:
            groups = [head]
    return groups


async def detect(images_b64: list[str]) -> dict:
    """Nhận diện trang GCN từ list ảnh đã render. Sampling tất định (chat_json
    mặc định temperature=0).

    Returns: {"gcn_pages": [idx,...], "start_pages": [idx,...]}
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
