"""Runner smoke E2E tập trung (nhiều stage) — hạ tầng THẬT (Mongo), đọc-only.

Theo house-style datalens (testing-and-eval.md §Hai tầng smoke): tầng PURE nằm
trong `__main__` từng module (vd `python -m src.extentions.multimodal.chu_cuoi`);
tầng E2E đụng store thật gom về ĐÂY theo stage. Thêm khả năng ⇒ thêm 1 stage +
đăng ký `_STAGES` (+ dọn trong `cleanup()` nếu stage có ghi — hiện mọi stage
đọc-only nên cleanup rỗng).

Gate hạ tầng: nối Mongo lỗi ⇒ stage tự SKIP (không KILL) để phần khác vẫn chạy.
KILL đỏ ⇒ dừng, sửa GỐC — không vá vội.

Chạy TRONG CONTAINER:
    docker compose exec api python -m app.scripts.smoke chu_cuoi_real
    docker compose exec api python -m app.scripts.smoke chu_cuoi_real --limit 50
"""

import argparse
import asyncio
import sys

from app import config
from app.db import gcns
from src.extentions.multimodal.chu_cuoi import chu_cuoi_for_result


class Kill(Exception):
    """Bất biến vỡ — stage FAIL."""


class Skip(Exception):
    """Thiếu hạ tầng — stage bỏ qua, không tính FAIL."""


def _entries(doc: dict):
    for rec in doc.get("extractions") or []:
        if isinstance(rec, dict) and isinstance(rec.get("result"), dict):
            yield rec["result"]


async def _sample_docs(match: dict, limit: int) -> list[dict]:
    try:
        cur = gcns().aggregate([{"$match": match}, {"$sample": {"size": limit}},
                                {"$project": {"extractions": 1}}])
        return [d async for d in cur]
    except Exception as e:  # noqa: BLE001 — Mongo không nối được / chưa có data
        raise Skip(f"không lấy được mẫu từ Mongo: {e}") from e


async def stage_chu_cuoi_real(limit: int) -> None:
    """chu_cuoi_for_result trên doc CÓ biến động thật — không crash + đo tỉ lệ 'thap'.

    docker compose exec api python -m app.scripts.smoke chu_cuoi_real
    """
    docs = await _sample_docs(
        {"extractions.result.Đăng ký.Biến động.Nội dung biến động": {"$exists": True, "$ne": ""}},
        limit)
    if not docs:
        raise Skip("không có doc nào có biến động")

    n_cc = n_entry = 0
    nguon = {"bien_dong": 0, "giay_goc": 0}
    conf = {"cao": 0, "thap": 0}
    id_la_giay_goc = 0  # Số giấy tờ giấy-gốc lệch 9/12 (chất lượng OCR, KHÔNG kill)
    thap_no_chu = 0     # chuyển chủ nhưng regex KHÔNG bóc được chủ nào → cần LLM
    thap_no_date = 0    # bóc được chủ nhưng thiếu ngày (nhẹ, chủ vẫn dùng được)
    fail_texts: list[str] = []  # Nội dung biến động của ca chu=[] — đầu vào luyện LLM

    for doc in docs:
        gid = doc.get("_id", "?")
        for result in _entries(doc):
            try:
                ccs = chu_cuoi_for_result(result)
            except Exception as e:  # noqa: BLE001
                raise Kill(f"chu_cuoi CRASH trên doc {gid}: {e}") from e
            reg = result.get("Đăng ký") or []
            if len(ccs) != len([e for e in reg if isinstance(e, dict)]):
                raise Kill(f"doc {gid}: số chu_cuoi ({len(ccs)}) ≠ số entry Đăng ký")
            for cc, entry in zip(ccs, reg):
                n_entry += 1
                nguon[cc["nguon"]] = nguon.get(cc["nguon"], 0) + 1
                conf[cc["confidence"]] = conf.get(cc["confidence"], 0) + 1
                if cc["nguon"] == "bien_dong":
                    n_cc += 1
                # Bất biến: Số giấy tờ mình TỰ BÓC từ biến động phải đúng 9/12 chữ
                # số (RE_CCCD đảm bảo) — sai = bắt nhầm số hồ sơ. Số bê nguyên từ
                # giấy gốc là do VLM/OCR, lệch thì đếm để biết, KHÔNG kill.
                for c in cc["chu"]:
                    sg = c.get("Số giấy tờ") or ""
                    if not sg:
                        continue
                    ok = sg.isdigit() and len(sg) in (9, 12)
                    if cc["nguon"] == "bien_dong" and not ok:
                        raise Kill(f"doc {gid}: Số giấy tờ bóc từ biến động sai định dạng: {sg!r}")
                    if cc["nguon"] == "giay_goc" and not ok:
                        id_la_giay_goc += 1
                # Bất biến: có chủ gốc mà chu rỗng ở nhánh giay_goc = mất người
                if cc["nguon"] == "giay_goc" and not cc["chu"]:
                    goc = [c for c in (entry.get("Chủ sử dụng") or [])
                           if isinstance(c, dict) and (c.get("Tên chủ") or "").strip()]
                    if goc:
                        raise Kill(f"doc {gid}: giay_goc mất chủ (gốc có {len(goc)}, chu cuối 0)")
                if cc["confidence"] == "thap" and cc["nguon"] == "bien_dong":
                    if not cc["chu"]:
                        thap_no_chu += 1
                        idx = cc["bien_dong_index"]
                        bds = entry.get("Biến động") or []
                        if isinstance(idx, int) and 0 <= idx < len(bds) and len(fail_texts) < 25:
                            fail_texts.append(bds[idx].get("Nội dung biến động") or "")
                    else:
                        thap_no_date += 1

    print(f"\n  doc {len(docs)} · entry {n_entry} · từ biến động {n_cc} · từ giấy gốc {nguon['giay_goc']}")
    tot = max(n_entry, 1)
    print(f"  nguồn   : bien_dong {nguon['bien_dong']} ({nguon['bien_dong']/tot*100:.1f}%)"
          f"  ·  giay_goc {nguon['giay_goc']} ({nguon['giay_goc']/tot*100:.1f}%)")
    print(f"  độ tin  : cao {conf['cao']} ({conf['cao']/tot*100:.1f}%)"
          f"  ·  THAP {conf['thap']} ({conf['thap']/tot*100:.1f}%)")
    ccb = max(n_cc, 1)
    print(f"  trong THAP: chu=[] {thap_no_chu} ({thap_no_chu/ccb*100:.1f}% giao dịch)"
          f" ← CẦN LLM  ·  thiếu-ngày {thap_no_date} (chủ vẫn ok)")
    if id_la_giay_goc:
        print(f"  ghi chú : {id_la_giay_goc} Số giấy tờ giấy-gốc lệch 9/12 (OCR, không kill)")
    if fail_texts:
        print(f"\n  TEXT ca chu=[] ({len(fail_texts)}) — regex bóc chủ HỤT, đầu vào luyện LLM:")
        for t in fail_texts:
            print(f"    · {_clip(t)}")


def _clip(s: str, n: int = 320) -> str:
    import re
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s if len(s) <= n else s[:n] + " …"


async def stage_chu_cuoi_llm(limit: int) -> None:
    """LLM text-only trên ĐÚNG các ca regex bó tay (chu=[]) — đo recover thật.

    Cần vLLM endpoint (VLLM_BASE_URL). KHÔNG ghi DB.
    docker compose exec api python -m app.scripts.smoke chu_cuoi_llm --limit 40
    """
    from src.extentions.multimodal.chu_cuoi import chu_cuoi_for_entry
    from src.extentions.multimodal.chu_cuoi_llm import refine_chu_cuoi

    docs = await _sample_docs(
        {"extractions.result.Đăng ký.Biến động.Nội dung biến động": {"$exists": True, "$ne": ""}},
        limit)
    if not docs:
        raise Skip("không có doc nào có biến động")

    hard: list[tuple[dict, dict]] = []  # (cc, entry) các ca chu=[] cần LLM
    for doc in docs:
        for result in _entries(doc):
            for entry in result.get("Đăng ký") or []:
                if not isinstance(entry, dict):
                    continue
                cc = chu_cuoi_for_entry(entry)
                if cc["nguon"] == "bien_dong" and cc["confidence"] == "thap" and not cc["chu"]:
                    hard.append((cc, entry))

    if not hard:
        print("  không có ca chu=[] trong mẫu (regex đã phủ hết) — tăng --limit")
        return

    recovered = 0
    for cc, entry in hard:
        idx = cc["bien_dong_index"]
        text = (entry.get("Biến động") or [])[idx].get("Nội dung biến động", "")
        new = await refine_chu_cuoi(cc, entry)
        if new.get("chu"):
            recovered += 1
            names = [c["Tên chủ"] for c in new["chu"]]
            # Bất biến cấu trúc LLM: tên không rỗng, Số giấy tờ (nếu có) 9/12 hoặc Hộ chiếu
            for c in new["chu"]:
                if not (c.get("Tên chủ") or "").strip():
                    raise Kill(f"LLM trả chủ rỗng tên: {new}")
            print(f"  ✓ {names}\n      ← {_clip(text, 160)}")
        else:
            print(f"  · (LLM cũng chịu) {_clip(text, 160)}")

    print(f"\n  ca chu=[] {len(hard)} · LLM recover {recovered} "
          f"({recovered/len(hard)*100:.0f}%) · còn lại {len(hard)-recovered} → hậu kiểm")


async def stage_backfill_idem(limit: int) -> None:
    """Backfill idempotent: regex chạy 2 lần cho CÙNG kết quả (không LLM cho tất định).

    docker compose exec api python -m app.scripts.smoke backfill_idem --limit 100
    """
    from src.extentions.multimodal.chu_cuoi import chu_cuoi_for_result

    docs = await _sample_docs({"extractions": {"$exists": True, "$ne": []}}, limit)
    if not docs:
        raise Skip("không có doc nào")
    for doc in docs:
        for result in _entries(doc):
            a = chu_cuoi_for_result(result)
            b = chu_cuoi_for_result(result)
            if a != b:
                raise Kill(f"doc {doc.get('_id')}: regex KHÔNG tất định (2 lần khác nhau)")
    print(f"  {len(docs)} doc · regex tất định (2 lần == nhau) ✓")


async def stage_cut_diagnose(limit: int) -> None:
    """PURE — chẩn đoán cắt sai từ chuỗi nhãn (không Mongo, không GPU).

    Ca chuẩn lấy từ hồ sơ thật D 0161849 (6 trang: bìa · chứng nhận · trích lục ·
    biến động · CCCD trước · CCCD sau) — đúng dạng file mà pipeline đang cắt hỏng.

    docker compose exec api python -m app.scripts.smoke cut_diagnose
    """
    from app.scripts.audit_cut_vlm import _diagnose
    from app.scripts.bench_pipeline import _groups_from_roles

    def chan(roles):
        g = _groups_from_roles(roles)
        return g, " ‖ ".join(_diagnose(roles, g))

    dung = ["cover", "content", "content", "content", "other", "other"]
    g, d = chan(dung)
    if g != [[0, 1, 2, 3]]:
        raise Kill(f"[1] nhãn ĐÚNG phải ra 1 nhóm 4 trang, ra {g}")
    if "LỖI" in d or "XEM LẠI" in d:
        raise Kill(f"[2] nhãn ĐÚNG (CCCD ở cuối bị loại là hợp lệ) mà vẫn báo lỗi: {d}")

    # Nghi phạm A — CCCD bị đọc thành bìa → đẻ ra GCN giả ở cuối file.
    a = ["cover", "content", "content", "content", "cover", "content"]
    g, d = chan(a)
    if len(g) != 2:
        raise Kill(f"[3] CCCD-thành-bìa phải ra 2 nhóm, ra {g}")
    if "XEM LẠI" not in d:
        raise Kill(f"[4] không nêu nghi vấn nhóm ngắn bám đuôi file: {d}")

    # Nghi phạm B — trích lục xoay ngang bị gán 'other' → NUỐT trang biến động.
    b = ["cover", "content", "other", "content", "other", "other"]
    g, d = chan(b)
    if 3 in {i for gg in g for i in gg}:
        raise Kill(f"[5] trang biến động (tr.4) đáng lẽ bị rớt, nhóm ra {g}")
    if "LỖI 1" not in d:
        raise Kill(f"[6] không nhận ra 'other' đóng nhóm nuốt trang sau: {d}")

    # Lỗi 2 — content lạc trước khi có bìa nào → bỏ im lặng.
    c = ["content", "content", "cover", "content"]
    g, d = chan(c)
    if g != [[2, 3]]:
        raise Kill(f"[7] content lạc phải bị bỏ, nhóm ra {g}")
    if "LỖI 2" not in d:
        raise Kill(f"[8] không nhận ra content lạc không bìa: {d}")

    # Không được báo bừa: 'other' ở CUỐI file là bình thường (giấy tờ kèm).
    e = ["cover", "content", "other", "other"]
    _, d = chan(e)
    if "LỖI 1" in d:
        raise Kill(f"[9] báo nhầm: 'other' ở cuối file là hợp lệ, không nuốt gì: {d}")

    print("  5 ca chẩn đoán (đúng · bìa giả · nuốt biến động · content lạc · other cuối) ✓")


# đăng ký stage: tên → (hàm, mô tả)
_STAGES = {
    "cut_diagnose": (stage_cut_diagnose, "PURE — chẩn đoán cắt sai từ chuỗi nhãn"),
    "chu_cuoi_real": (stage_chu_cuoi_real, "chu_cuoi trên doc có biến động thật"),
    "chu_cuoi_llm": (stage_chu_cuoi_llm, "LLM text-only cứu ca regex bó tay (cần vLLM)"),
    "backfill_idem": (stage_backfill_idem, "regex tất định (idempotent) cho backfill"),
}


async def cleanup() -> None:
    """Mọi stage hiện đọc-only → không có gì phải dọn."""
    return None


async def main() -> int:
    p = argparse.ArgumentParser(description="Runner smoke E2E (Mongo thật, đọc-only).")
    p.add_argument("stages", nargs="*", help=f"stage cần chạy: {', '.join(_STAGES)}")
    p.add_argument("--limit", type=int, default=20, help="Số doc lấy mẫu mỗi stage.")
    p.add_argument("--list", action="store_true", help="Liệt kê stage rồi thoát.")
    args = p.parse_args()

    if args.list or not args.stages:
        print("Stage khả dụng:")
        for name, (_, desc) in _STAGES.items():
            print(f"  {name:<16} {desc}")
        return 0

    print(f"DB: {config.MONGO_URI}/{config.MONGO_DB}  ·  limit {args.limit}")
    n_fail = n_skip = 0
    for name in args.stages:
        if name not in _STAGES:
            print(f"\n[{name}] KHÔNG có stage này — bỏ qua")
            n_fail += 1
            continue
        fn, _ = _STAGES[name]
        print(f"\n=== {name} ===")
        try:
            await fn(args.limit)
            print(f"[{name}] PASS")
        except Skip as e:
            print(f"[{name}] SKIP — {e}")
            n_skip += 1
        except Kill as e:
            print(f"[{name}] KILL — {e}")
            n_fail += 1
        except Exception as e:  # noqa: BLE001 — lỗi ngoài dự kiến = FAIL
            print(f"[{name}] KILL (lỗi lạ) — {type(e).__name__}: {e}")
            n_fail += 1

    await cleanup()
    print(f"\n{'─'*50}\nTổng: {len(args.stages)} stage · FAIL {n_fail} · SKIP {n_skip}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
