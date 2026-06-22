import re
from typing import Optional, Tuple


def extract_gcn_id(file_key: str) -> Tuple[Optional[str], str]:
    filename = file_key.split('/')[-1].rsplit('.', 1)[0].replace('D0', 'DO')

    if re.search(r'[-_]GT\b', filename, re.IGNORECASE):
        return None, "skipped_gt"

    m = re.search(r'GCN', filename, re.IGNORECASE)
    if not m:
        return None, "manual_review"
    
    if "GCN" not in file_key.upper():
        return None, "manual_review"

    raw_after  = filename[m.end():].strip(' -_.')
    raw_before = filename[:m.start()].strip(' -_.')

    def is_clean_end(raw: str, end_pos: int) -> bool:
        """True nếu end_pos là hết chuỗi hoặc chỉ còn separator không có chữ/số sau."""
        if end_pos >= len(raw):
            return True
        rest = raw[end_pos:]
        if re.match(r'^[A-Za-z0-9\u00C0-\u024F]', rest):
            return False
        if re.match(r'^-[A-Za-z0-9]', rest):
            return False
        return True

    def strip_random_suffix(raw: str) -> str:
        """
        Cắt '-XxXxXx' mixed chữ+số ở cuối (random suffix do hệ thống sinh ra).
        Không cắt nếu suffix toàn chữ hoặc toàn số.
        """
        match = re.search(r'-([A-Za-z0-9]{4,})$', raw)
        if not match:
            return raw
        suffix = match.group(1)
        if re.search(r'[A-Za-z]', suffix) and re.search(r'[0-9]', suffix):
            return raw[:match.start()].strip()
        return raw

    def try_prefix_digits(raw: str):
        """1-2 chữ cái Unicode + space tùy chọn + digits (≥4)."""
        match = re.match(r'^([^\W\d_]{1,2})\s*(\d{4,})', raw, re.UNICODE)
        if not match:
            return None
        prefix      = match.group(1).upper()
        digits_full = match.group(2)
        max_digits  = 8 if prefix == "AA" else 6
        for take in dict.fromkeys([min(max_digits, len(digits_full)), len(digits_full)]):
            digits  = digits_full[:take]
            end_pos = match.start(2) + take
            if is_clean_end(raw, end_pos):
                return f"{prefix} {digits}", "success"
        return None

    def try_pure_digits(raw: str, min_len: int = 5):
        """
        Digits thuần ≤15, chỉ khi raw không bắt đầu bằng chữ cái.
        min_len: độ dài tối thiểu để tránh lấy số ngắn vô nghĩa (mặc định ≥5).
        """
        if re.match(r'^[A-Za-z\u00C0-\u024F]', raw):
            return None
        match = re.match(r'^\d{1,15}', raw)
        if not match:
            return None
        if len(match.group()) < min_len:
            return None
        if is_clean_end(raw, match.end()):
            return match.group(), "success"
        return None

    def try_all(raw: str, min_pure_digits: int = 5):
        """Thử trên raw gốc và raw đã strip random suffix."""
        for candidate in dict.fromkeys([raw, strip_random_suffix(raw)]):
            r = try_prefix_digits(candidate)
            if r:
                return r
            r = try_pure_digits(candidate, min_len=min_pure_digits)
            if r:
                return r
        return None

    def search_prefix_digits(raw: str):
        """
        Like try_prefix_digits but searches the whole string (not just ^).
        Returns the rightmost prefix+digits match with a clean end.
        Used for before_gcn where junk like '435. ' may precede the real ID.
        """
        best = None
        for m in re.finditer(r'([^\W\d_]{1,2})\s*(\d{4,})', raw, re.UNICODE):
            prefix      = m.group(1).upper()
            digits_full = m.group(2)
            max_digits  = 8 if prefix == "AA" else 6
            for take in dict.fromkeys([min(max_digits, len(digits_full)), len(digits_full)]):
                digits  = digits_full[:take]
                end_pos = m.start(2) + take
                if is_clean_end(raw, end_pos):
                    best = f"{prefix} {digits}", "success"
                    break
        return best

    def longest_digits_in(raw: str, min_len: int = 5) -> Optional[str]:
        """Tìm chuỗi digits dài nhất (≥ min_len) với clean end trong raw."""
        best = None
        for dm in re.finditer(r'\d+', raw):
            digits = dm.group()
            if len(digits) >= min_len and is_clean_end(raw, dm.end()):
                if best is None or len(digits) > len(best):
                    best = digits
        return best

    # ------------------------------------------------------------------
    # 1. after_gcn: prefix+digits hoặc pure-digits (≥5 chars) → success
    # ------------------------------------------------------------------
    if raw_after:
        r = try_all(raw_after, min_pure_digits=5)
        if r:
            return r

    # ------------------------------------------------------------------
    # 2. before_gcn: prefix+digits only (and strip suffix) → success
    #    Uses search_prefix_digits which scans the whole string, so it
    #    handles junk prefixes like "435. " before "Đ 406526".
    # ------------------------------------------------------------------
    if raw_before:
        for candidate in dict.fromkeys([raw_before, strip_random_suffix(raw_before)]):
            r = search_prefix_digits(candidate)
            if r:
                return r

    # ------------------------------------------------------------------
    # 3. before_gcn: longest digits → success if ≥11 (unambiguous official
    #    ID), manual_review if 5-10 (could be page number, date, etc.)
    # ------------------------------------------------------------------
    if raw_before:
        best = longest_digits_in(raw_before, min_len=5)
        if best:
            confidence = "success" if len(best) >= 8 else "manual_review"
            return best, confidence

    # ------------------------------------------------------------------
    # 4. after_gcn: pure-digits short (≥2) last resort
    #    ≥11 digits → success (official ID); shorter → manual_review
    # ------------------------------------------------------------------
    if raw_after and re.match(r'^\d+$', strip_random_suffix(raw_after)):
        r = try_all(raw_after, min_pure_digits=2)
        if r:
            gcn_id, _ = r
            confidence = "success" if len(gcn_id) >= 8 else "manual_review"
            return gcn_id, confidence

    return None, "manual_review"


if __name__ == "__main__":
    cases = [
        ("00001/H26.14.4.19.2025.22.02.00.006279/00001-GCN-AA 00811545.pdf",
            ('AA 00811545', 'success')),
        ("00001/HSG-T-364492-2018/00001-GCN-CO 111138_.pdf",
            ('CO 111138', 'success')),
        ("00001/HSG-T-995833-2022/00001-GCN-DĐ 154851.pdf",
            ('DĐ 154851', 'success')),
        ("00004/.2025.22.02.00.0/00004-GCN-AA 05558097 2510210589.pdf",
            ('AA 05558097', 'success')),
        ("00004/H26.14.4.19.2026.22.02.00.59/00004-GCN-AA 07035205-e5s9YMma.pdf",
            ('AA 07035205', 'success')),
        ("00007/2018.22.02.00.17007280/00007-GCN-CQ 192202-YNmb1G5U.pdf",
            ('CQ 192202', 'success')),
        ("00004/DC 999167/DC 999167-GCN.pdf",
            ('DC 999167', 'success')),
        ("hsq-hni/huyen-me-linh/HSQ-29-11/New folder (2)/CO 294091762-GCN.pdf",
            ('CO 294091762', 'success')),
        ("00025/010117634904512/010117634904512-GCN.pdf",
            ('010117634904512', 'success')),
        ("phuong-khuong-dinh/ScanKhuongDinh_AI/10111031216(chưa nộp gcn)-GT.pdf",
            (None, 'skipped_gt')),
        ("hsq-hni/huyen-me-linh/HSQ-29-11/New folder (4)/CO 623239 (2)-GCN.pdf",
            ('CO 623239', 'success')),
        ("00091/HSG-T-1366585-2024/00091-GCN-DN 88620.pdf",
            ('DN 88620', 'success')),
        ("00622/2003.22.02.00.101213/00622-GCN-10121315216-o4THh8vB.pdf",
            ('10121315216', 'success')),
        ("huyen-chuong-my/PL03-01 xã Hoà Phú/ScanGCN_HoaPhu/42 Nguyễn Văn Thọ_0001_blurry_seri-GCN.pdf",
            (None, 'manual_review')),
        ("huyen-chuong-my/HSQ_PL03/HÒA PHÚ/PHU NAM AN/AP 393477-GCN-3.pdf",
            ('AP 393477', 'success')),
        ("huyen-gia-lam/HSQPL03-1/28.11.2025/HSQPL03-1/Phù Đổng/PD/354. 10119055838-GCN.pdf",
            ('10119055838', 'success')),
        ("huyen-gia-lam/HSQPL03-1/28.11.2025/HSQPL03-1/Phù Đổng/PD/435. A Đ 406526-GCN.pdf",
         ('Đ 406526', 'success')),
        ("huyen-gia-lam/HSQPL03-1/28.11.2025/HSQPL03-1/Phù Đổng/PD/100. Mờ - GCN_0001.pdf",
         ("0001", "manual_review")),
        ("huyen-thanh-tri/HSQTHEUDS01/hsq_Thanh Tri/012323 - 00249-GCN.pdf",
         ("012323", "manual_review")),
        ("Chung cu/20012026/TRANG3/ĐỐNGĐA/0109176566-GCN.pdf",
        ("0109176566", "success")),
        ("00007/13364/13364-GCN.pdf",('13364', 'manual_review')),
        ("Chung cu/26122025/FILE MỚI/NAM TỪ LIÊM/AA 04463858 (251120-0122).pdf",(None, 'manual_review')),
    ]

    all_pass = True
    for file_key, expected in cases:
        result = extract_gcn_id(file_key)
        ok = result == expected
        if not ok:
            all_pass = False
        mark = "✅" if ok else "❌"
        print(f"{mark} got {result!r:35s} expected {expected!r:35s} | {file_key.split('/')[-1]}")

    print("\n" + ("✅ ALL PASSED" if all_pass else "❌ SOME FAILED"))