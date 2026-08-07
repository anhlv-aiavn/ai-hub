import asyncio
import json
import logging
import os
import time

from dotenv import load_dotenv

load_dotenv()

import litellm
from litellm import acompletion

litellm.turn_off_message_logging = True
litellm.suppress_logs = True

_litellm_logger = logging.getLogger("LiteLLM")
_litellm_logger.setLevel(logging.CRITICAL)
_litellm_logger.propagate = False

log = logging.getLogger(__name__)

VLLM_BASE_URL = os.getenv("VLLM_BASE_URL", "http://192.168.120.12:2900/v1")
VLLM_MODEL_NAME = os.getenv("VLLM_MODEL_NAME", "cyankiwi/gemma-4-26B-A4B-it-AWQ-4bit")
VLLM_API_KEY = os.getenv("VLLM_API_KEY", "EMPTY")
ENABLE_THINKING = os.getenv("ENABLE_THINKING", "true").strip().lower() == "true"

# Trần call ĐỒNG THỜI cho MỖI endpoint (mỗi máy vLLM = 1 GPU). Thêm máy → tổng
# năng lực = PER_ENDPOINT × số endpoint, tự nhân lên, KHÔNG phải sửa chỗ khác.
PER_ENDPOINT_CONCURRENCY = int(os.getenv("MAX_VLM_CONCURRENT", "8"))

# Circuit-breaker: máy vừa lỗi kết nối bị "làm nguội" N giây — trong lúc đó
# _pick_least né nó, khỏi phí một lần thử-máy-chết mỗi request (máy tắt luôn có
# inflight=0 nên nếu không có cái này nó lại là "máy rảnh nhất" được chọn TRƯỚC).
# 0 = tắt tính năng (giữ hành vi cũ: chọn thuần theo inflight).
ENDPOINT_COOLDOWN = float(os.getenv("VLM_ENDPOINT_COOLDOWN_SECONDS", "15"))


# ── Khai báo endpoint ────────────────────────────────────────────────────────
#
# Khai báo nhiều máy vLLM qua env VLLM_ENDPOINTS (danh sách ngăn cách bởi dấu
# phẩy). Mỗi mục là "url" (dùng chung model/key mặc định) hoặc "url|model|key"
# nếu máy đó khác model/key:
#
#   VLLM_ENDPOINTS=http://192.168.120.9:30000/v1,http://192.168.120.10:30001/v1
#
# Bỏ trống VLLM_ENDPOINTS → quay về 1 endpoint VLLM_BASE_URL (tương thích ngược).

def _parse_endpoint_specs(raw: str, d_url: str, d_model: str, d_key: str) -> list[tuple]:
    """raw → [(url, model, key)]. THUẦN (không tạo semaphore) để smoke test được.
    Mục thiếu model/key thì lấy mặc định; rỗng → 1 endpoint mặc định."""
    specs = [s.strip() for s in raw.split(",") if s.strip()] if raw.strip() else [d_url]
    out = []
    for spec in specs:
        parts = [p.strip() for p in spec.split("|")]
        url = parts[0]
        model = parts[1] if len(parts) > 1 and parts[1] else d_model
        key = parts[2] if len(parts) > 2 and parts[2] else d_key
        out.append((url, model, key))
    return out


class _Endpoint:
    """1 máy vLLM: base_url + model + key, kèm semaphore riêng (trần/máy) và bộ
    đếm việc-đang-chạy để cân tải."""

    __slots__ = ("base_url", "model", "api_key", "sem", "inflight", "cooldown_until")

    def __init__(self, base_url: str, model: str, api_key: str):
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self.sem = asyncio.Semaphore(PER_ENDPOINT_CONCURRENCY)
        self.inflight = 0
        # Mốc time.monotonic() mà máy này hết "nguội" (được chọn lại). 0 = khỏe.
        self.cooldown_until = 0.0


_POOL: list[_Endpoint] | None = None


def _pool() -> list[_Endpoint]:
    """Pool endpoint, dựng LƯỜI (trong event loop) — theo đúng nếp _VLM_SEM cũ."""
    global _POOL
    if _POOL is None:
        specs = _parse_endpoint_specs(
            os.getenv("VLLM_ENDPOINTS", ""), VLLM_BASE_URL, VLLM_MODEL_NAME, VLLM_API_KEY)
        _POOL = [_Endpoint(u, m, k) for u, m, k in specs]
        log.info("VLM pool: %d endpoint · trần/máy=%d · [%s]",
                 len(_POOL), PER_ENDPOINT_CONCURRENCY, ", ".join(e.base_url for e in _POOL))
    return _POOL


def total_vlm_concurrency() -> int:
    """Tổng năng lực = trần/máy × số máy — run_job dùng để đặt trần fan-out (bound RAM)."""
    return PER_ENDPOINT_CONCURRENCY * len(_pool())


def endpoint_count() -> int:
    return len(_pool())


def _pick_least(pool: list, tried: list) -> "_Endpoint":
    """Chọn endpoint ÍT VIỆC NHẤT trong số CHƯA THỬ (cân tải + failover), ưu tiên
    máy KHÔNG đang cooldown (né máy vừa lỗi). Nếu mọi máy còn lại đều đang cooldown
    thì vẫn chọn 1 để PROBE — không bao giờ trả None, để vòng lặp kết thúc bằng
    raise thay vì kẹt. THUẦN (đồng bộ, không await) nên chọn-rồi-tăng-inflight là
    nguyên tử trong asyncio."""
    now = time.monotonic()
    cands = [e for e in pool if e not in tried] or pool
    healthy = [e for e in cands if e.cooldown_until <= now]
    return min(healthy or cands, key=lambda e: e.inflight)


# ── Gọi VLM ──────────────────────────────────────────────────────────────────

# Lỗi ĐÁNG chuyển máy khác (kết nối/quá tải/sự cố nội bộ). vLLM tự host hay trả
# lỗi kết nối dưới dạng InternalServerError nên gộp vào đây. Các lỗi khác (nội
# dung bị từ chối, vượt context, JSON hỏng) KHÔNG failover — ném thẳng.
_FAILOVER_ERRORS = tuple(
    e for e in (
        getattr(litellm, "APIConnectionError", None),
        getattr(litellm, "Timeout", None),
        getattr(litellm, "InternalServerError", None),
        getattr(litellm, "ServiceUnavailableError", None),
        getattr(litellm, "RateLimitError", None),
    ) if e is not None
)


def _images_to_content(images_b64: list[str]) -> list[dict]:
    return [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b}"}}
        for b in images_b64
    ]


async def _call_endpoint(ep: "_Endpoint", messages: list, extra: dict,
                         temperature: float, repetition_penalty: float,
                         max_tokens: int | None = None) -> dict:
    response = await acompletion(
        model=f"openai/{ep.model}",
        api_base=ep.base_url,
        api_key=ep.api_key,
        temperature=temperature,
        repetition_penalty=repetition_penalty,
        response_format={"type": "json_object"},
        messages=messages,
        **({"max_tokens": max_tokens} if max_tokens else {}),
        **extra,
    )
    content = response.choices[0].message.content
    if isinstance(content, (dict, list)):
        return content
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return {"raw": content}


async def chat_json(
    *,
    system_prompt: str,
    user_text: str,
    images_b64: list[str],
    enable_thinking: bool | None = None,
    temperature: float = 0.0,
    repetition_penalty: float = 1.0,
    max_tokens: int | None = None,
) -> dict:
    """Gọi VLM, ép response_format JSON, trả về dict đã parse.

    Chia việc: chọn endpoint ít việc nhất trong pool, giữ trần riêng mỗi máy; một
    máy lỗi kết nối/quá tải thì tự thử máy còn lại (failover). Thêm máy chỉ cần
    khai VLLM_ENDPOINTS, không đụng code.

    enable_thinking: None→env, True→bật CoT (retry), False→tắt.
    Sampling: temperature=0 (tất định) + repetition_penalty=1; không ép top_p/k.

    max_tokens: TRẦN token sinh ra. None = không đặt (theo server). Đặt trần rất
    đáng khi bật thinking: một lần model lảm nhảm/lặp vòng là ngốn hàng nghìn
    token, mà GPU đang decode-bound (~14 tok/s mỗi request khi 43 request chạy
    song song) nên một ca như thế giữ chỗ hàng phút, kéo tụt cả hàng đợi.
    """
    use_thinking = ENABLE_THINKING if enable_thinking is None else enable_thinking
    extra = (
        {"extra_body": {
            "chat_template_kwargs": {"enable_thinking": True},
            "skip_special_tokens": False,
        }}
        if use_thinking else {}
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": [
            {"type": "text", "text": user_text},
            *_images_to_content(images_b64),
        ]},
    ]

    pool = _pool()
    tried: list = []
    last_exc: Exception | None = None
    for _ in range(len(pool)):
        ep = _pick_least(pool, tried)
        tried.append(ep)
        ep.inflight += 1  # tăng NGAY (đồng bộ) để lần chọn kế cân sang máy khác
        try:
            async with ep.sem:
                result = await _call_endpoint(ep, messages, extra, temperature,
                                              repetition_penalty, max_tokens)
            ep.cooldown_until = 0.0  # gọi được → gỡ cooldown ngay (half-open đã khỏi)
            return result
        except _FAILOVER_ERRORS as e:
            ep.cooldown_until = time.monotonic() + ENDPOINT_COOLDOWN  # làm nguội máy lỗi
            last_exc = e
            log.warning("VLM %s lỗi (%s) — cooldown %.0fs, chuyển endpoint khác",
                        ep.base_url, type(e).__name__, ENDPOINT_COOLDOWN)
        finally:
            ep.inflight -= 1
    # Mọi endpoint đều lỗi kết nối → ném lỗi cuối để run_job xử (retry mức doc).
    raise last_exc if last_exc else RuntimeError("Không có VLM endpoint nào khả dụng")


# ── PURE smoke ───────────────────────────────────────────────────────────────

def _smoke() -> None:
    d = ("http://def/v1", "gemma", "EMPTY")

    r = _parse_endpoint_specs("", *d)
    assert r == [("http://def/v1", "gemma", "EMPTY")], f"KILL [1] rỗng→mặc định: {r}"

    r = _parse_endpoint_specs("http://a/v1, http://b/v1", *d)
    assert r == [("http://a/v1", "gemma", "EMPTY"), ("http://b/v1", "gemma", "EMPTY")], \
        f"KILL [2] 2 url dùng model/key mặc định: {r}"

    r = _parse_endpoint_specs("http://a/v1|mA|kA,http://b/v1", *d)
    assert r[0] == ("http://a/v1", "mA", "kA"), f"KILL [3] override/máy: {r}"
    assert r[1] == ("http://b/v1", "gemma", "EMPTY"), f"KILL [4] máy sau vẫn mặc định: {r}"

    r = _parse_endpoint_specs("  http://a/v1  ,  ,  ", *d)
    assert r == [("http://a/v1", "gemma", "EMPTY")], f"KILL [5] bỏ khoảng trắng/mục rỗng: {r}"

    # url|model (thiếu key) → key mặc định
    r = _parse_endpoint_specs("http://a/v1|mA", *d)
    assert r == [("http://a/v1", "mA", "EMPTY")], f"KILL [6] thiếu key→mặc định: {r}"

    # Chọn ít-việc-nhất + failover bỏ máy đã thử.
    eps = [_Endpoint("http://a/v1", "m", "k"), _Endpoint("http://b/v1", "m", "k")]
    eps[0].inflight, eps[1].inflight = 2, 0
    assert _pick_least(eps, []) is eps[1], "KILL [7] chọn máy ít việc"
    eps[0].inflight, eps[1].inflight = 0, 3
    assert _pick_least(eps, []) is eps[0], "KILL [8] đổi tải → đổi lựa chọn"
    assert _pick_least(eps, [eps[0]]) is eps[1], "KILL [9] failover: bỏ máy đã thử"
    # Đã thử hết → vẫn trả 1 máy (không None) để vòng lặp kết thúc bằng raise.
    assert _pick_least(eps, eps) in eps, "KILL [10] thử hết vẫn trả 1 máy"

    # Cooldown: né máy vừa lỗi DÙ nó rảnh hơn (inflight thấp hơn).
    eps[0].inflight, eps[1].inflight = 5, 0
    eps[1].cooldown_until = time.monotonic() + 999
    assert _pick_least(eps, []) is eps[0], "KILL [11] né máy đang cooldown dù nó rảnh hơn"
    # Mọi máy đều cooldown → vẫn PROBE (không kẹt), chọn máy ít việc nhất.
    eps[0].cooldown_until = time.monotonic() + 999
    assert _pick_least(eps, []) is eps[1], "KILL [12] tất cả cooldown → vẫn probe máy ít việc"
    # Cooldown đã hết hạn (mốc trong quá khứ) → coi như khỏe lại.
    eps[0].cooldown_until = eps[1].cooldown_until = time.monotonic() - 1
    assert _pick_least(eps, []) is eps[1], "KILL [13] hết hạn cooldown → khỏe lại, chọn theo inflight"

    print("vlm_client PURE: 13 KILL ✓")


if __name__ == "__main__":
    _smoke()
