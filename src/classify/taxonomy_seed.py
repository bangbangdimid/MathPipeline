"""Tier-1 (lĩnh vực) and tier-2 (chuyên đề) taxonomy + keyword maps.

Keywords are ACCENT-STRIPPED lowercase (matched against textnorm.normalize output),
so they survive OCR diacritic loss ("đường tròn" -> "duong tron").
"""
from __future__ import annotations

TAXONOMY = {
    "Hình học": {
        "kw": ["tam giac", "duong tron", "duong cao", "tiep tuyen", "goc", "dien tich",
               "vuong goc", "trung diem", "duong kinh", "day cung", "noi tiep",
               "phan giac", "hinh thang", "hinh vuong", "hinh chu nhat", "chu vi",
               "dong quy", "thang hang", "duong trung tuyen", "cung"],
        "sub": {
            "Đường tròn": ["duong tron", "tiep tuyen", "day cung", "duong kinh",
                           "noi tiep", "tiep diem", "cung"],
            "Tam giác": ["tam giac", "duong cao", "trung tuyen", "phan giac", "dong dang"],
            "Hệ thức lượng": ["he thuc luong", "sin", "cos", "tan", "cosin"],
            "Diện tích & chu vi": ["dien tich", "chu vi"],
        },
    },
    "Số học": {
        "kw": ["chia het", "so nguyen to", "nguyen to", "dong du", "uoc chung",
               "boi chung", "nghiem nguyen", "chinh phuong", "so nguyen"],
        "sub": {
            "Chia hết & đồng dư": ["chia het", "dong du", "uoc", "boi"],
            "Số nguyên tố": ["so nguyen to", "nguyen to"],
            "Phương trình nghiệm nguyên": ["nghiem nguyen", "so nguyen x", "so nguyen x va y"],
            "Số chính phương": ["chinh phuong"],
        },
    },
    "Bất đẳng thức": {
        "kw": ["bat dang thuc", "gia tri nho nhat", "gia tri lon nhat", "nho nhat",
               "lon nhat", "am gm", "cauchy", "bunhiacop"],
        "sub": {
            "Cực trị": ["gia tri nho nhat", "gia tri lon nhat", "nho nhat", "lon nhat"],
            "BĐT cổ điển": ["am gm", "cauchy", "bunhiacop"],
        },
    },
    "Đại số": {
        "kw": ["rut gon", "bieu thuc", "can bac hai", "can bac", "phan thuc",
               "da thuc", "hang dang thuc", "nhan tu"],
        "sub": {
            "Rút gọn biểu thức": ["rut gon", "bieu thuc", "phan thuc"],
            "Căn thức": ["can bac hai", "can bac"],
            "Đa thức & phân tích": ["da thuc", "nhan tu", "hang dang thuc"],
        },
    },
    "Phương trình & Hệ": {
        "kw": ["phuong trinh", "he phuong trinh", "bat phuong trinh",
               "giai phuong trinh", "nghiem"],
        "sub": {
            "Phương trình": ["phuong trinh", "giai phuong trinh"],
            "Hệ phương trình": ["he phuong trinh"],
        },
    },
    "Tổ hợp": {
        "kw": ["to hop", "dem so", "chinh hop", "hoan vi", "dirichlet",
               "bat bien", "nguyen ly"],
        "sub": {
            "Đếm": ["dem so", "chinh hop", "hoan vi"],
            "Nguyên lý Dirichlet": ["dirichlet"],
            "Bất biến / đơn biến": ["bat bien"],
        },
    },
    "Hàm số": {
        "kw": ["ham so", "do thi", "parabol", "duong thang y"],
        "sub": {
            "Đồ thị & tương giao": ["do thi", "parabol", "tuong giao"],
        },
    },
}


def seed_taxonomy(conn) -> dict:
    """Insert tier-1/2 nodes if empty; return {name: taxonomy_id}."""
    have = conn.execute("SELECT COUNT(*) c FROM taxonomy").fetchone()["c"]
    if not have:
        for t1, spec in TAXONOMY.items():
            cur = conn.execute(
                "INSERT INTO taxonomy (tier,parent_id,name) VALUES (1,NULL,?)", (t1,))
            pid = cur.lastrowid
            for t2 in spec["sub"]:
                conn.execute(
                    "INSERT INTO taxonomy (tier,parent_id,name) VALUES (2,?,?)", (pid, t2))
        conn.commit()
    return {r["name"]: r["id"]
            for r in conn.execute("SELECT id,name FROM taxonomy")}
