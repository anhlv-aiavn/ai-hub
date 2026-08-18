"""Đo THẲNG một call extract: số token đầu ra + có reasoning không + thời gian —
để biết 117s/hồ sơ đi vào đâu (nghi: model reasoning sinh token thừa dù
ENABLE_THINKING=false, vì code không gửi enable_thinking:false tường minh).

So 3 chế độ trên CÙNG 1 ảnh:
  - implicit_off : y hệt production hiện tại (extra_body rỗng, phó mặc template).
  - explicit_off : ép chat_template_kwargs.enable_thinking=false.
  - thinking_on  : ép bật (để thấy trần token khi reasoning).

Nếu implicit_off có completion_tokens/reasoning CAO ≈ thinking_on → template đang
reasoning mặc định = thủ phạm; chuyển sang explicit_off sẽ cắt token → nhanh hơn
(nhớ EVAL chất lượng trước khi đổi mặc định).

Chạy TRONG CONTAINER worker (tới được vLLM):
    docker compose exec worker python -m app.scripts.probe_vlm /path/to.pdf
    docker compose exec worker python -m app.scripts.probe_vlm a.pdf --pages 0,1
"""

import argparse
import asyncio
import io
import time

from litellm import acompletion

from src.extentions.multimodal.make import pdf_to_corrected_images
from src.extentions.multimodal.prompt import extract_system_prompt, pdf_extract_prompt
from src.extentions.multimodal.vlm_client import _images_to_content, _pool

_MODES = {
    "implicit_off": {},  # == production hiện tại
    "explicit_off": {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
    "thinking_on": {"extra_body": {"chat_template_kwargs": {"enable_thinking": True},
                                   "skip_special_tokens": False}},
}


async def _call(ep, images_b64, extra):
    messages = [
        {"role": "system", "content": extract_system_prompt},
        {"role": "user", "content": [
            {"type": "text", "text": pdf_extract_prompt},
            *_images_to_content(images_b64),
        ]},
    ]
    t0 = time.monotonic()
    resp = await acompletion(
        model=f"openai/{ep.model}", api_base=ep.base_url, api_key=ep.api_key,
        temperature=0.0, repetition_penalty=1.0,
        response_format={"type": "json_object"}, messages=messages, **extra,
    )
    dt = time.monotonic() - t0
    msg = resp.choices[0].message
    usage = resp.usage or {}
    reasoning = getattr(msg, "reasoning_content", None) or ""
    content = msg.content or ""
    return {
        "dt": dt,
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "reasoning_chars": len(reasoning),
        "content_chars": len(content),
    }


async def run(pdf_path: str, pages: list[int] | None) -> None:
    with open(pdf_path, "rb") as f:
        buf = io.BytesIO(f.read())
    images = pdf_to_corrected_images(buf, dpi=200, max_img_size=2000)
    if pages:
        images = [images[i] for i in pages if 0 <= i < len(images)]
    print(f"  {len(images)} ảnh · endpoint {_pool()[0].base_url}\n", flush=True)
    print(f"  {'mode':<13} {'giây':>7} {'prompt_tok':>11} {'compl_tok':>10} "
          f"{'reason_ch':>10} {'json_ch':>8}", flush=True)
    loi: list[str] = []
    for name, extra in _MODES.items():
        try:
            r = await _call(_pool()[0], images, extra)
            print(f"  {name:<13} {r['dt']:>7.1f} {str(r['prompt_tokens']):>11} "
                  f"{str(r['completion_tokens']):>10} {r['reasoning_chars']:>10} "
                  f"{r['content_chars']:>8}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  {name:<13} lỗi: {e}", flush=True)
            loi.append(name)

    # Không đo được thì KHÔNG kết luận. Bản trước in nguyên câu "template reasoning
    # là thủ phạm" kể cả khi cả 3 mode đều lỗi — một kết luận tự tin về phép đo
    # chưa từng xảy ra, đúng loại output khiến người đọc truy sai hướng.
    if len(loi) == len(_MODES):
        print("\n  → KHÔNG đo được mode nào: mọi call đều lỗi (xem thông báo ở trên). "
              "Lỗi 'No connected db' là litellm proxy bắt xác thực nhưng không nối DB "
              "— gửi đúng master key vào VLLM_API_KEY, hoặc trỏ VLLM_ENDPOINTS thẳng "
              "vào vLLM. Chưa có số liệu nào để kết luận về reasoning.", flush=True)
        return
    if loi:
        print(f"\n  → thiếu {len(loi)}/{len(_MODES)} mode ({', '.join(loi)}) — "
              "so sánh dưới đây chỉ dựa trên các mode chạy được.", flush=True)
    print("\n  → implicit_off có compl_tok/reason ≈ thinking_on ⇒ template reasoning mặc định"
          " (thủ phạm). explicit_off cắt token ⇒ nhanh hơn (eval chất lượng trước khi đổi).",
          flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--pages", default=None, help="vd 0,1 — chỉ dùng vài trang")
    args = ap.parse_args()
    pages = [int(x) for x in args.pages.split(",")] if args.pages else None
    asyncio.run(run(args.pdf, pages))


if __name__ == "__main__":
    main()
