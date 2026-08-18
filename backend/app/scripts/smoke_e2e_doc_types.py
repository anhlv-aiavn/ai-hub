"""Smoke E2E cho các LOẠI GIẤY mới (ddk · kqdk · pcctt) — đi ĐÚNG đường người dùng đi.

Khác `app/scripts/smoke.py` (đọc Mongo, đọc-only): script này **gọi API qua HTTP**,
đẩy PDF thật lên, chờ worker xử lý, rồi soi kết quả trả về. Nó đo cả chuỗi:
    login → POST /v1/batches (doc_type) → worker claim → render → dedup trang →
    classify lọc trang → extract → ghi Mongo → GET /v1/gcn/{id}
Không import gì của app, chỉ dùng thư viện chuẩn — chạy được từ máy server, từ
laptop, hay trong container, miễn là với tới được API.

    # trong container
    docker compose exec api python -m app.scripts.smoke_e2e_doc_types \
        --api http://localhost:8000 -u admin -p '***' \
        --loai pcctt "tmp/Phiếu CCTT"

    # từ máy server (cổng host, xem README)
    python3 backend/app/scripts/smoke_e2e_doc_types.py \
        --api http://localhost:18002 -u admin -p '***' \
        --loai ddk tmp/don1.pdf tmp/don2.pdf

    # cả ba loại một lượt, mỗi loại 5 file đầu, lưu JSON để soi tay
    python3 ... --bo-ba "tmp/Phiếu CCTT - Đơn ĐK - Kết quả ĐK" --so-luong 5 --json /tmp/out

HAI TẦNG KẾT LUẬN, cố tình tách bạch:

  KILL  — bất biến KỸ THUẬT vỡ: sai loại giấy, sai khóa, một file ra nhiều hồ sơ,
          thân JSON sai hình dạng, trang trỏ ra ngoài phạm vi. Sai ở đây là LỖI CODE,
          exit code ≠ 0.

  Độ điền — tỉ lệ % mỗi trường bóc ra được. KHÔNG kill: ba loại này dữ liệu gần như
          toàn bộ là CHỮ VIẾT TAY, một ô trống có thể do người dân bỏ trống thật.
          Đây là con số để bạn tự quyết prompt đã đủ tốt chưa, và để so trước/sau
          mỗi lần sửa prompt.

Script KHÔNG tự xoá lô (mặc định) để bạn mở UI soi tiếp; thêm `--xoa` nếu muốn dọn.
"""

import argparse
import json
import mimetypes
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

# ── Loại giấy: khóa thân JSON + các trường dùng để đo độ điền ───────────────
#
# Đường dẫn trường viết dạng "a.b.c"; phần tử "[]" nghĩa là mảng — điền được khi
# mảng có ít nhất một phần tử có giá trị ở nhánh còn lại.
LOAI = {
    "pcctt": {
        "than": "Phiếu thu thập",
        "truong": [
            "Người sử dụng đất.Họ và tên người đại diện",
            "Người sử dụng đất.Số giấy tờ",
            "Người sử dụng đất.Địa chỉ",
            "Thửa đất.Thửa đất số",
            "Thửa đất.Tờ bản đồ số",
            "Thửa đất.Địa chỉ",
            "Thửa đất.Diện tích",
            "Thửa đất.Mục đích sử dụng",
            "Thửa đất.Nguồn gốc sử dụng",
            "Ngày lập",
            "Người cung cấp thông tin",
        ],
    },
    "ddk": {
        "than": "Đơn đăng ký",
        "truong": [
            "Thông tin đơn.Kính gửi",
            "Thông tin đơn.Ngày ký",
            "Người sử dụng đất.Họ và tên",
            "Người sử dụng đất.Giấy tờ nhân thân",
            "Người sử dụng đất.Địa chỉ",
            "Thửa đất.Thửa đất số",
            "Thửa đất.Tờ bản đồ số",
            "Thửa đất.Địa chỉ",
            "Thửa đất.Diện tích",
            "Thửa đất.Mục đích sử dụng",
            "Thửa đất.Nguồn gốc sử dụng",
            "Nhà ở, công trình xây dựng.Loại",
            "Đề nghị.Cấp Giấy chứng nhận",
            "Giấy tờ nộp kèm[]",
            "Người sử dụng chung[].Tên",
        ],
    },
    "kqdk": {
        "than": "Giấy xác nhận",
        "truong": [
            "Thông tin văn bản.Cơ quan cấp",
            "Thông tin văn bản.Số văn bản",
            "Thông tin văn bản.Ngày ký",
            "Thông tin văn bản.Người ký",
            "Thông tin văn bản.Số vào sổ ĐKĐĐ",
            "Người sử dụng đất[].Họ và tên",
            "Người sử dụng đất[].Số giấy tờ",
            "Thửa đất.Thửa đất số",
            "Thửa đất.Tờ bản đồ số",
            "Thửa đất.Diện tích",
            "Thửa đất.Nguồn gốc sử dụng",
            "Xác nhận của UBND.Tình trạng tranh chấp",
            "Xác nhận của UBND.Sự phù hợp với quy hoạch",
        ],
    },
}

# Tên thư mục trong bộ mẫu khách gửi → mã loại (dùng cho --bo-ba).
THU_MUC_BO_BA = {"Phiếu CCTT": "pcctt", "Đơn ĐK": "ddk", "Kết quả ĐK": "kqdk"}


class Kill(Exception):
    """Bất biến kỹ thuật vỡ."""


# ── HTTP thuần thư viện chuẩn ───────────────────────────────────────────────

class Api:
    def __init__(self, goc: str, bo_qua_ssl: bool = False, timeout: int = 120):
        self.goc = goc.rstrip("/")
        self.token = None
        self.timeout = timeout
        self.ctx = ssl._create_unverified_context() if bo_qua_ssl else None

    def _goi(self, method: str, duong: str, *, body: bytes = None,
             ctype: str = None, params: dict = None) -> dict:
        url = f"{self.goc}{duong}"
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        req = urllib.request.Request(url, data=body, method=method)
        if ctype:
            req.add_header("Content-Type", ctype)
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.ctx) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            chi_tiet = e.read().decode("utf-8", "replace")[:500]
            raise Kill(f"{method} {duong} → HTTP {e.code}: {chi_tiet}") from e
        except urllib.error.URLError as e:
            raise Kill(f"{method} {duong} → không nối được API ({e.reason}). "
                       f"Kiểm tra --api (trong container thường là http://localhost:8000, "
                       f"từ máy host là http://localhost:18002)") from e
        return json.loads(raw) if raw else {}

    def get(self, duong: str, **params) -> dict:
        return self._goi("GET", duong, params=params or None)

    def post_json(self, duong: str, data: dict) -> dict:
        return self._goi("POST", duong, body=json.dumps(data).encode(),
                         ctype="application/json")

    def delete(self, duong: str) -> dict:
        return self._goi("DELETE", duong)

    def dang_nhap(self, user: str, mat_khau: str) -> None:
        d = self.post_json("/v1/auth/login", {"username": user, "password": mat_khau})
        self.token = d.get("token")
        if not self.token:
            raise Kill(f"login không trả token: {d}")

    def tai_len(self, duong_tep: str, doc_type: str, batch_id: str | None,
                ten_lo: str | None) -> dict:
        """POST /v1/batches multipart — TỰ dựng body, không dùng requests để script
        chạy được bằng python3 trần trên máy server."""
        bien = f"----smoke{uuid.uuid4().hex}"
        with open(duong_tep, "rb") as f:
            noi_dung = f.read()
        mime = mimetypes.guess_type(duong_tep)[0] or "application/pdf"
        phan = []

        def truong(ten: str, gt: str):
            phan.append(f"--{bien}\r\nContent-Disposition: form-data; name=\"{ten}\"\r\n\r\n"
                        f"{gt}\r\n".encode())

        truong("doc_type", doc_type)
        if batch_id:
            truong("batch_id", batch_id)
        elif ten_lo:
            truong("name", ten_lo)
        phan.append(
            f"--{bien}\r\nContent-Disposition: form-data; name=\"files\"; "
            f"filename=\"{os.path.basename(duong_tep)}\"\r\n"
            f"Content-Type: {mime}\r\n\r\n".encode())
        phan.append(noi_dung)
        phan.append(f"\r\n--{bien}--\r\n".encode())
        return self._goi("POST", "/v1/batches", body=b"".join(phan),
                         ctype=f"multipart/form-data; boundary={bien}")


# ── Đọc dữ liệu theo đường dẫn "a.b[].c" ────────────────────────────────────

def _co_gia_tri(v) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, dict)):
        return len(v) > 0
    return True


def lay(node, duong: str):
    """Giá trị theo đường dẫn; "[]" = duyệt mảng, trả phần tử ĐẦU TIÊN có giá trị."""
    cur = node
    for i, khuc in enumerate(duong.split(".")):
        mang = khuc.endswith("[]")
        ten = khuc[:-2] if mang else khuc
        if ten:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(ten)
        if mang:
            if not isinstance(cur, list):
                return None
            con = ".".join(duong.split(".")[i + 1:])
            if not con:
                return cur if cur else None
            for e in cur:
                v = lay(e, con)
                if _co_gia_tri(v):
                    return v
            return None
    return cur


# ── Kiểm tra một hồ sơ ──────────────────────────────────────────────────────

def kiem_tra(doc: dict, ma_loai: str, ten_tep: str) -> tuple[list[str], dict]:
    """Trả (danh sách KILL, {trường: có/không}). KHÔNG raise — gom hết lỗi của mọi
    file rồi báo một lượt, chạy 30 file mà chết ở file thứ 2 thì mất công chờ."""
    kills: list[str] = []
    cfg = LOAI[ma_loai]
    ten_than = cfg["than"]

    if doc.get("doc_type") != ma_loai:
        kills.append(f"doc_type trả về {doc.get('doc_type')!r} ≠ {ma_loai!r} đã gửi")

    khoa_mong_doi = os.path.splitext(ten_tep)[0]
    tom = doc.get("summary") or {}
    if tom.get("khoa_chinh") != khoa_mong_doi:
        kills.append(f"khoa_chinh={tom.get('khoa_chinh')!r}, phải bằng tên tệp {khoa_mong_doi!r}")
    if doc.get("group_key") != khoa_mong_doi:
        kills.append(f"group_key={doc.get('group_key')!r} ≠ khóa nguồn {khoa_mong_doi!r}")
    # Khóa biểu mẫu KHÔNG được lọt vào index dò trùng Số phát hành của GCN.
    if doc.get("extracted_so_phat_hanhs"):
        kills.append(f"extracted_so_phat_hanhs phải rỗng với loại {ma_loai}, "
                     f"đang là {doc['extracted_so_phat_hanhs']}")

    recs = doc.get("extractions") or []
    if len(recs) != 1:
        kills.append(f"một file phải ra ĐÚNG một hồ sơ, đang có {len(recs)} bản ghi")
    rows = doc.get("gcn_rows") or []
    if len(rows) > 1:
        kills.append(f"gcn_rows phải ≤1 hàng, đang có {len(rows)}")

    than = {}
    if recs:
        res = recs[0].get("result")
        if not isinstance(res, dict):
            kills.append(f"result không phải object: {type(res).__name__}")
        elif ten_than not in res:
            kills.append(f"thiếu khóa thân {ten_than!r}, chỉ thấy {list(res)[:4]}")
        elif not isinstance(res[ten_than], dict):
            kills.append(f"{ten_than!r} phải là object, đang là {type(res[ten_than]).__name__}")
        else:
            than = res[ten_than]
        # Trang đưa vào extract phải nằm trong phạm vi trang thật của file.
        n_trang = doc.get("page_count") or 0
        xau = [i for i in (recs[0].get("page_indices") or [])
               if not isinstance(i, int) or i < 0 or i >= n_trang]
        if xau:
            kills.append(f"page_indices ngoài phạm vi 0..{n_trang - 1}: {xau}")

    dien = {t: _co_gia_tri(lay(than, t)) for t in cfg["truong"]}
    return kills, dien


# ── Chạy một loại ───────────────────────────────────────────────────────────

def _tep_pdf(duong: list[str], so_luong: int | None) -> list[str]:
    ra: list[str] = []
    for d in duong:
        if os.path.isdir(d):
            ra += [os.path.join(d, f) for f in sorted(os.listdir(d))
                   if f.lower().endswith(".pdf")]
        elif d.lower().endswith(".pdf"):
            ra.append(d)
    return ra[:so_luong] if so_luong else ra


def cho_xong(api: Api, batch_id: str, tong: int, timeout: int) -> dict:
    """Chờ worker xử lý hết lô. Đọc `counts` của lô (counter worker duy trì) thay vì
    poll từng doc — cùng nguồn số liệu mà UI dùng."""
    het = time.monotonic() + timeout
    truoc = None
    while time.monotonic() < het:
        b = api.get(f"/v1/batches/{batch_id}")
        c = b.get("counts") or {}
        con = (c.get("queued", 0)) + (c.get("processing", 0))
        dong = f"    {tong - con}/{tong} xong · " + " ".join(
            f"{k}={v}" for k, v in sorted(c.items()) if v)
        if dong != truoc:
            print(dong, flush=True)
            truoc = dong
        if con == 0:
            return c
        time.sleep(5)
    raise Kill(f"quá {timeout}s mà lô chưa xử lý xong — worker chết? "
               f"kiểm tra `docker compose logs -f worker`")


def chay_loai(api: Api, ma_loai: str, duong: list[str], args) -> tuple[int, int]:
    teps = _tep_pdf(duong, args.so_luong)
    if not teps:
        print(f"\n[{ma_loai}] không tìm thấy PDF nào trong {duong} — bỏ qua")
        return 0, 0

    ten_lo = f"SMOKE {ma_loai} {time.strftime('%d/%m %H:%M:%S')}"
    print(f"\n{'=' * 78}\n[{ma_loai}] {len(teps)} tệp · lô \"{ten_lo}\"\n{'=' * 78}")

    batch_id = None
    gui_loi: list[str] = []
    for t in teps:
        try:
            d = api.tai_len(t, ma_loai, batch_id, ten_lo)
            batch_id = d["batch_id"]
        except Kill as e:
            gui_loi.append(f"{os.path.basename(t)}: {e}")
    if not batch_id:
        raise Kill(f"[{ma_loai}] không tải lên được tệp nào:\n  " + "\n  ".join(gui_loi))
    print(f"  batch_id = {batch_id}")

    dem = cho_xong(api, batch_id, len(teps) - len(gui_loi), args.timeout)

    # Lấy id mọi doc trong lô rồi đọc chi tiết từng cái.
    ds = api.get("/v1/gcn", batch_id=batch_id, page_size=200, doc_type=ma_loai)
    ids = list(dict.fromkeys(r["gcn_id"] for r in ds.get("gcn", []) if r.get("gcn_id")))

    kills: list[str] = [f"[{ma_loai}] tải lên thất bại — {x}" for x in gui_loi]
    thong_ke: dict[str, int] = {}
    n_ok = 0
    print(f"\n  {'tệp':<26}{'trạng thái':<11}{'trang':>10}  {'giây':>6}  điền")
    print(f"  {'-' * 74}")
    for gid in ids:
        doc = api.get(f"/v1/gcn/{gid}")
        ten = doc.get("filename") or gid
        tt = doc.get("status")
        recs = doc.get("extractions") or []
        tm = doc.get("timings") or {}
        n_trang = doc.get("page_count") or 0
        n_dung = len(recs[0].get("page_indices") or []) if recs else 0
        n_trung = tm.get("trang_trung", 0)
        giay = tm.get("pipeline")

        if tt != "done":
            kills.append(f"[{ma_loai}] {ten}: status={tt} · {doc.get('error')}")
            print(f"  {ten[:25]:<26}{tt:<11}{n_trang:>10}  {'-':>6}  ✗ {doc.get('error') or ''}")
            continue

        k, dien = kiem_tra(doc, ma_loai, ten)
        kills += [f"[{ma_loai}] {ten}: {x}" for x in k]
        for truong, co in dien.items():
            thong_ke[truong] = thong_ke.get(truong, 0) + (1 if co else 0)
        n_ok += 1

        tr = f"{n_dung}/{n_trang}" + (f" -{n_trung}" if n_trung else "")
        tick = "✗" if k else "·"
        print(f"  {ten[:25]:<26}{tt:<11}{tr:>10}  {giay or 0:>6.0f}  "
              f"{tick} {sum(dien.values())}/{len(dien)}")

        if args.json:
            os.makedirs(args.json, exist_ok=True)
            with open(os.path.join(args.json, f"{ma_loai}-{os.path.splitext(ten)[0]}.json"),
                      "w", encoding="utf-8") as f:
                json.dump({"summary": doc.get("summary"), "timings": tm,
                           "extractions": recs}, f, ensure_ascii=False, indent=2)

    print(f"\n  cột trang = số trang đưa vào VLM / tổng số trang (-N = số trang trùng đã loại)")

    if n_ok:
        print(f"\n  ĐỘ ĐIỀN từng trường ({n_ok} hồ sơ) — thấp không phải lỗi code, "
              f"xem lại prompt hoặc do dân bỏ trống:")
        for truong in LOAI[ma_loai]["truong"]:
            c = thong_ke.get(truong, 0)
            pct = c / n_ok * 100
            thanh = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
            print(f"    {thanh} {pct:5.1f}%  {truong}")

    if args.xoa:
        api.delete(f"/v1/batches/{batch_id}")
        print(f"\n  đã xoá lô {batch_id}")
    else:
        print(f"\n  lô GIỮ LẠI để soi trên UI: {batch_id}  (thêm --xoa để tự dọn)")

    for x in kills:
        print(f"  KILL {x}")
    return len(kills), dem.get("done", 0)


# ── Tự kiểm bộ kiểm tra (không cần server) ──────────────────────────────────

def _tu_kiem() -> None:
    """Soi chính `lay()` và `kiem_tra()` bằng doc giả. Chạy trước khi ra server:
    bộ kiểm tra sai thì hoặc báo động giả, hoặc tệ hơn — nuốt lỗi thật."""
    assert lay({"a": {"b": "x"}}, "a.b") == "x", "KILL [1] đường dẫn lồng"
    assert lay({"a": {}}, "a.b") is None, "KILL [2] thiếu nấc cuối"
    assert lay({}, "a.b.c") is None, "KILL [3] đứt ngay nấc đầu"
    assert lay({"a": "chuỗi"}, "a.b") is None, "KILL [4] nấc giữa không phải dict"
    ds = {"ng": [{"t": ""}, {"t": "Duy"}]}
    assert lay(ds, "ng[].t") == "Duy", "KILL [5] mảng → phần tử ĐẦU TIÊN có giá trị"
    assert lay({"ng": []}, "ng[].t") is None, "KILL [6] mảng rỗng"
    assert lay({"g": ["a"]}, "g[]") == ["a"], "KILL [7] mảng lá"
    assert lay({"g": []}, "g[]") is None, "KILL [8] mảng lá rỗng"

    assert _co_gia_tri("x") and not _co_gia_tri("   "), "KILL [9] chuỗi trắng = trống"
    assert not _co_gia_tri(None) and not _co_gia_tri([]) and not _co_gia_tri({}), "KILL [10]"
    assert _co_gia_tri(False) and _co_gia_tri(0), "KILL [11] false/0 vẫn là ĐÃ ĐIỀN"

    tot = {
        "doc_type": "pcctt", "page_count": 4, "group_key": "1218602",
        "extracted_so_phat_hanhs": [],
        "summary": {"khoa_chinh": "1218602"},
        "gcn_rows": [{"khoa_chinh": "1218602"}],
        "extractions": [{"page_indices": [0], "result": {"Phiếu thu thập": {
            "Người sử dụng đất": {"Họ và tên người đại diện": "LÊ VĂN TRƯỜNG",
                                   "Số giấy tờ": "019084001782", "Địa chỉ": "Thôn Đo"},
            "Thửa đất": {"Thửa đất số": "57", "Tờ bản đồ số": "18", "Diện tích": "204.6",
                          "Địa chỉ": "Thôn Đo (cũ), xã Trung Giã",
                          "Mục đích sử dụng": "Đất ở", "Nguồn gốc sử dụng": "Các cụ để lại"},
            "Ngày lập": "", "Người cung cấp thông tin": "Lê Văn Trường"}}}],
    }
    k, dien = kiem_tra(tot, "pcctt", "1218602.pdf")
    assert k == [], f"KILL [12] hồ sơ hợp lệ không được báo lỗi: {k}"
    assert dien["Ngày lập"] is False, "KILL [13] ô trống → chưa điền"
    assert dien["Thửa đất.Diện tích"] is True, "KILL [14] ô có giá trị → đã điền"
    assert sum(dien.values()) == len(dien) - 1, "KILL [15] đếm đúng 1 ô trống"

    # Mỗi hỏng một kiểu → phải bắt được ĐÚNG một lỗi tương ứng.
    def hong(sua: dict, manh: str, ten_tep="1218602.pdf"):
        d = json.loads(json.dumps(tot))
        d.update(sua)
        loi, _ = kiem_tra(d, "pcctt", ten_tep)
        assert any(manh in x for x in loi), f"KILL bỏ sót {manh!r}: {loi}"

    hong({"doc_type": "ddk"}, "doc_type trả về")                       # KILL [16]
    hong({"summary": {"khoa_chinh": "sai"}}, "khoa_chinh")             # KILL [17]
    hong({"group_key": None}, "group_key")                             # KILL [18]
    hong({"extracted_so_phat_hanhs": ["AA 123"]}, "phải rỗng")         # KILL [19]
    hong({"extractions": []}, "ĐÚNG một hồ sơ")                        # KILL [20]
    hong({"gcn_rows": [{}, {}]}, "gcn_rows")                           # KILL [21]
    hong({"extractions": [{"page_indices": [0], "result": {"Đơn đăng ký": {}}}]},
         "thiếu khóa thân")                                            # KILL [22]
    hong({"extractions": [{"page_indices": [0], "result": "chuỗi"}]},
         "không phải object")                                          # KILL [23]
    hong({"extractions": [{"page_indices": [9], "result": tot["extractions"][0]["result"]}]},
         "ngoài phạm vi")                                              # KILL [24]
    # Đổi tên tệp = đổi khóa mong đợi — chốt "khóa bám tên tệp", không bám nội dung.
    hong({}, "khoa_chinh", ten_tep="tep-khac.pdf")                     # KILL [25]

    # Loại khác dùng đúng khóa thân của nó.
    kq = {"doc_type": "kqdk", "page_count": 2, "group_key": "253008",
          "extracted_so_phat_hanhs": [], "summary": {"khoa_chinh": "253008"},
          "gcn_rows": [{}],
          "extractions": [{"page_indices": [0, 1], "result": {"Giấy xác nhận": {
              "Thông tin văn bản": {"Số văn bản": "108/GXN-VPĐKĐĐTT"},
              "Người sử dụng đất": [{"Họ và tên": "TRẦN KIM DUY"}]}}}]}
    k2, d2 = kiem_tra(kq, "kqdk", "253008.pdf")
    assert k2 == [], f"KILL [26] kqdk hợp lệ: {k2}"
    assert d2["Người sử dụng đất[].Họ và tên"] is True, "KILL [27] đọc qua mảng người"
    assert d2["Thửa đất.Thửa đất số"] is False, "KILL [28] thiếu thửa → chưa điền"

    print("smoke_e2e_doc_types tự kiểm: 28 KILL ✓")


def main() -> int:
    p = argparse.ArgumentParser(
        description="Smoke E2E cho ddk/kqdk/pcctt — đẩy PDF qua API thật rồi soi kết quả.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    p.add_argument("--api", default=os.getenv("AIHUB_API", "http://localhost:18002"),
                   help="Gốc API. Trong container: http://localhost:8000 (mặc định: %(default)s)")
    p.add_argument("-u", "--user", default=os.getenv("AIHUB_USER", "admin"))
    p.add_argument("-p", "--password", default=os.getenv("AIHUB_PASSWORD"))
    p.add_argument("--loai", choices=sorted(LOAI), help="Loại giấy của các đường dẫn truyền vào.")
    p.add_argument("duong", nargs="*", help="Tệp PDF hoặc thư mục chứa PDF.")
    p.add_argument("--bo-ba", metavar="THƯ_MỤC",
                   help="Thư mục chứa 3 thư mục con 'Phiếu CCTT' / 'Đơn ĐK' / 'Kết quả ĐK' "
                        "— chạy cả ba loại một lượt.")
    p.add_argument("--so-luong", type=int, help="Chỉ lấy N tệp đầu mỗi loại (khuyến nghị 5 khi thử).")
    p.add_argument("--timeout", type=int, default=1800, help="Giây chờ tối đa mỗi lô (%(default)s).")
    p.add_argument("--json", metavar="THƯ_MỤC", help="Lưu kết quả JSON từng tệp để soi tay.")
    p.add_argument("--xoa", action="store_true", help="Xoá lô sau khi kiểm tra xong.")
    p.add_argument("--bo-qua-ssl", action="store_true", help="Bỏ kiểm chứng chỉ HTTPS.")
    p.add_argument("--tu-kiem", action="store_true",
                   help="Tự kiểm bộ kiểm tra bằng doc giả rồi thoát — không cần server, "
                        "không cần GPU. Chạy cái này trước khi thử thật.")
    args = p.parse_args()

    if args.tu_kiem:
        _tu_kiem()
        return 0
    if not args.password:
        p.error("thiếu mật khẩu: dùng -p hoặc biến môi trường AIHUB_PASSWORD")
    viec: list[tuple[str, list[str]]] = []
    if args.bo_ba:
        for ten_tm, ma in THU_MUC_BO_BA.items():
            d = os.path.join(args.bo_ba, ten_tm)
            if os.path.isdir(d):
                viec.append((ma, [d]))
            else:
                print(f"cảnh báo: không thấy thư mục {d!r}")
    elif args.loai and args.duong:
        viec.append((args.loai, args.duong))
    else:
        p.error("cần --bo-ba THƯ_MỤC, hoặc --loai <ma> kèm danh sách tệp/thư mục")

    api = Api(args.api, args.bo_qua_ssl)
    print(f"API {args.api} · đăng nhập {args.user}…")
    try:
        api.dang_nhap(args.user, args.password)
    except Kill as e:
        # Không nhả traceback: lỗi ở đây là cấu hình (sai cổng / sai mật khẩu),
        # thông báo đã đủ rõ, stack chỉ làm loãng.
        print(f"\nKHÔNG ĐĂNG NHẬP ĐƯỢC: {e}")
        if "401" in str(e):
            print("  → mật khẩu admin nằm ở AIHUB_ADMIN_PASS trong .env CỦA SERVER "
                  "(không phải .env.example): grep AIHUB_ADMIN .env")
            print("  → nếu mật khẩu đã đổi qua UI thì .env là giá trị cũ, dùng tài "
                  "khoản bạn đang đăng nhập web.")
        return 2

    tong_kill = tong_done = 0
    for ma, duong in viec:
        try:
            k, d = chay_loai(api, ma, duong, args)
        except Kill as e:
            print(f"  KILL [{ma}] {e}")
            k, d = 1, 0
        tong_kill += k
        tong_done += d

    print(f"\n{'=' * 78}")
    if tong_kill:
        print(f"KẾT LUẬN: ✗ {tong_kill} KILL · {tong_done} hồ sơ xử lý xong.")
        print("KILL = bất biến kỹ thuật vỡ (sai loại/khóa/hình dạng JSON) → sửa GỐC, không vá vội.")
        return 1
    print(f"KẾT LUẬN: ✓ không KILL nào · {tong_done} hồ sơ xử lý xong.")
    print("Độ điền thấp KHÔNG phải KILL — đọc bảng % ở trên để quyết có sửa prompt không.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Kill as e:
        print(f"\nDỪNG: {e}")
        sys.exit(2)
    except KeyboardInterrupt:
        print("\nĐã huỷ. Lô đã tạo vẫn nằm trên hệ thống — xoá tay trên UI nếu cần.")
        sys.exit(130)
