"""Client gọi `qc-scanner-server` NGOÀI (kiểm chất lượng ảnh/scan trước khi
OCR) — hợp đồng API đọc từ
https://github.com/Jester6136/qc_scanner/blob/main/docs/api.md (đọc offline,
CHƯA test sống: endpoint nội bộ `192.168.120.9` không tới được từ máy dev —
xác nhận lại khi cắm vào máy serve).

`POST /?format=json`, multipart 1 trường `file`, header `Authorization: Bearer
<key>`. 200/422 đều là JSON hợp lệ (verdict pass/warn/fail) — KHÔNG raise cho
verdict "fail", đó là kết quả hợp lệ (ảnh không dùng được), xử lý ở tầng gọi.
Chỉ raise cho lỗi TÍCH HỢP/HẠ TẦNG (401/400/503/mạng)."""

import asyncio
import logging

import httpx

from app import config

log = logging.getLogger(__name__)

_SEM: asyncio.Semaphore | None = None


def _sem() -> asyncio.Semaphore:
    """Semaphore RIÊNG cho qc-scanner-server — KHÔNG dùng chung _VLM_SEM của
    pipeline GCN (2 hạ tầng khác nhau, trần khác nhau)."""
    global _SEM
    if _SEM is None:
        _SEM = asyncio.Semaphore(config.QC_SCANNER_MAX_CONCURRENT)
    return _SEM


class QCError(Exception):
    """Lỗi khi gọi qc-scanner-server — KHÔNG phải phán quyết về ảnh."""


class QCAuthError(QCError):
    """401 — lỗi cấu hình API key phía mình. Đừng đánh dấu ảnh hỏng, đừng
    retry cùng key (đúng hợp đồng §2 xác thực)."""


class QCInputError(QCError):
    """400 — input không đánh giá được (PDF hỏng/quá trang/thiếu file). Lỗi
    VĨNH VIỄN theo hợp đồng §3 — đừng retry."""


class QCUnavailable(QCError):
    """503 (SERVER_BUSY/INFERENCE_FAILED/MODEL_MISSING) sau khi đã retry hết
    lượt, hoặc lỗi mạng/timeout — TẠM THỜI, đáng thử lại (claim lại item sau)."""


class QCResult:
    __slots__ = ("verdict", "reasons", "metrics", "page_count", "raw", "pages")

    def __init__(self, verdict: str, reasons: list, metrics: dict, page_count: int, raw: dict,
                 pages: list | None = None):
        self.verdict = verdict
        self.reasons = reasons
        self.metrics = metrics
        self.page_count = page_count
        self.raw = raw
        # `pages` = verdict/reasons TỪNG TRANG (khác `reasons` đã gộp-khử-trùng
        # theo code ở trên, mất thông tin "trang nào" — FE cần cái này để hiện
        # "trang X: lỗi/cảnh báo", xem qcSyncShared.VerdictBadge). PDF/ảnh 1
        # trang không có `pages[]` thật từ server → tự dựng 1 phần tử duy nhất
        # cho ĐỒNG NHẤT hình dạng dữ liệu ở tầng gọi (khỏi phải if/else 2 nơi).
        self.pages = pages if pages is not None else [{"page": 1, "verdict": verdict, "reasons": reasons or []}]


def _parse(body: dict) -> QCResult:
    """Chuẩn hoá cả 2 hình dạng phản hồi `?format=json` (hợp đồng §3): 1
    trang/PDF 1 trang có `reasons` phẳng; PDF nhiều trang có `pages[]` và
    `verdict` TOP-LEVEL đã là verdict gộp (trang tệ nhất) — không cần tự suy
    lại. `reasons` gộp cho thống kê là hợp nhất theo `code` (không lặp) từ mọi
    trang; `pages` (mới) giữ NGUYÊN chi tiết verdict/reasons từng trang cho FE
    hiện "trang nào lỗi, trang nào cảnh báo" (xem QCResult.pages)."""
    verdict = body.get("verdict")
    reasons = body.get("reasons")
    pages_raw = body.get("pages")
    if reasons is None and pages_raw:
        seen: set[str] = set()
        reasons = []
        for p in pages_raw:
            for r in p.get("reasons") or []:
                code = r.get("code")
                if code and code not in seen:
                    seen.add(code)
                    reasons.append(r)
    page_count = body.get("page_count") or len(pages_raw or []) or 1
    metrics = body.get("metrics") or {}
    pages = None
    if pages_raw:
        pages = [{"page": p.get("page"), "verdict": p.get("verdict"), "reasons": p.get("reasons") or []}
                 for p in pages_raw]
    return QCResult(verdict=verdict, reasons=reasons or [], metrics=metrics,
                     page_count=page_count, raw=body, pages=pages)


async def check_pdf(pdf_bytes: bytes, filename: str = "file.pdf") -> QCResult:
    """Gọi `POST /?format=json` cho 1 PDF (ảnh rời và PDF nhiều trang dùng
    chung endpoint — hợp đồng §3). `503` là mã DUY NHẤT nên retry (ảnh chưa
    được xử lý lần nào — không phải phán quyết về ảnh); `401`/`400` KHÔNG
    retry."""
    if not config.QC_SCANNER_API_KEY:
        raise QCAuthError("Chưa cấu hình QC_SCANNER_API_KEY")
    url = f"{config.QC_SCANNER_BASE_URL}/"
    headers = {"Authorization": f"Bearer {config.QC_SCANNER_API_KEY}"}

    async with _sem():
        attempt = 0
        while True:
            attempt += 1
            files = {"file": (filename, pdf_bytes, "application/pdf")}
            try:
                async with httpx.AsyncClient(timeout=config.QC_SCANNER_TIMEOUT) as client:
                    resp = await client.post(url, params={"format": "json"},
                                             headers=headers, files=files)
            except httpx.HTTPError as e:
                if attempt <= config.QC_SCANNER_RETRY_MAX:
                    await asyncio.sleep(min(2 ** attempt, 10))
                    continue
                raise QCUnavailable(f"Không kết nối được qc-scanner-server: {e}") from e

            if resp.status_code in (200, 422):
                return _parse(resp.json())
            if resp.status_code == 401:
                raise QCAuthError("qc-scanner-server từ chối API key (401)")
            if resp.status_code == 400:
                try:
                    detail = resp.json()
                except Exception:  # noqa: BLE001
                    detail = resp.text
                raise QCInputError(f"Input không đánh giá được (400): {detail}")
            if resp.status_code == 503:
                if attempt <= config.QC_SCANNER_RETRY_MAX:
                    retry_after = float(resp.headers.get("Retry-After") or min(2 ** attempt, 10))
                    await asyncio.sleep(retry_after)
                    continue
                raise QCUnavailable(f"qc-scanner-server quá tải (503) sau {attempt} lần thử")
            raise QCUnavailable(f"qc-scanner-server trả mã lạ ({resp.status_code}): {resp.text[:200]}")


if __name__ == "__main__":
    # PURE smoke (test_eval.md §1): parse hợp đồng JSON KHÔNG cần network thật
    # — endpoint nội bộ 192.168.120.9 không tới được từ máy dev.
    sample_single = {
        "verdict": "warn", "reasons": [{"code": "CLIPPED_EDGE", "severity": "warn"}],
        "metrics": {"blur_score": 48.4}, "corners": None, "image": "iVBORw0KGgo",
    }
    r1 = _parse(sample_single)
    assert r1.verdict == "warn" and r1.reasons[0]["code"] == "CLIPPED_EDGE" and r1.page_count == 1
    assert r1.pages == [{"page": 1, "verdict": "warn", "reasons": r1.reasons}]

    sample_multi = {
        "source": "pdf", "verdict": "fail", "page_count": 3,
        "pages": [
            {"page": 1, "verdict": "pass", "reasons": []},
            {"page": 2, "verdict": "fail", "reasons": [{"code": "BLURRY"}]},
            {"page": 3, "verdict": "warn", "reasons": [{"code": "GLARE"}]},
        ],
    }
    r2 = _parse(sample_multi)
    assert r2.verdict == "fail" and r2.page_count == 3
    assert {x["code"] for x in r2.reasons} == {"BLURRY", "GLARE"}
    assert [p["verdict"] for p in r2.pages] == ["pass", "fail", "warn"]

    print("qc_client PURE smoke: OK")
