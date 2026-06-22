import json
import logging
import os

from dotenv import load_dotenv

load_dotenv()

import litellm
from litellm import acompletion

litellm.turn_off_message_logging = True
litellm.suppress_logs = True

_litellm_logger = logging.getLogger("LiteLLM")
_litellm_logger.setLevel(logging.CRITICAL)
_litellm_logger.propagate = False

VLLM_BASE_URL = os.getenv("VLLM_BASE_URL", "http://192.168.120.12:2900/v1")
VLLM_MODEL_NAME = os.getenv("VLLM_MODEL_NAME", "cyankiwi/gemma-4-26B-A4B-it-AWQ-4bit")
VLLM_API_KEY = os.getenv("VLLM_API_KEY", "EMPTY")
ENABLE_THINKING = os.getenv("ENABLE_THINKING", "true").strip().lower() == "true"


def _images_to_content(images_b64: list[str]) -> list[dict]:
    return [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b}"}}
        for b in images_b64
    ]


async def chat_json(
    *,
    system_prompt: str,
    user_text: str,
    images_b64: list[str],
    enable_thinking: bool | None = None,
    temperature: float = 1.0,
    top_p: float = 0.4,
    top_k: int = 40,
    repetition_penalty: float = 1.2,
) -> dict:
    """Gọi VLM, ép response_format JSON, trả về dict đã parse.

    enable_thinking:
        None  → dùng env ENABLE_THINKING (mặc định)
        True  → bật chain-of-thought (dùng để retry lần 2 khi không khớp)
        False → tắt
    temperature/top_p/top_k/repetition_penalty: sampling. Detect nên dùng
    temperature thấp (~0) để ổn định, tất định; extract giữ mặc định.
    """
    use_thinking = ENABLE_THINKING if enable_thinking is None else enable_thinking
    extra = (
        {
            "extra_body": {
                "chat_template_kwargs": {"enable_thinking": True},
                "skip_special_tokens": False,
            }
        }
        if use_thinking
        else {}
    )

    response = await acompletion(
        model=f"openai/{VLLM_MODEL_NAME}",
        api_base=VLLM_BASE_URL,
        api_key=VLLM_API_KEY,
        temperature=temperature,
        top_p=top_p,
        top_k=top_k,
        repetition_penalty=repetition_penalty,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    *_images_to_content(images_b64),
                ],
            },
        ],
        **extra,
    )

    content = response.choices[0].message.content
    if isinstance(content, (dict, list)):
        return content
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return {"raw": content}
