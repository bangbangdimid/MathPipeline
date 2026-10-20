"""Gemini full-page OCR: page image -> high-fidelity LaTeX + preserved figures.

Higher quality than the local hybrid (correct Vietnamese diacritics, math, layout
= no 'tofu' squares). Figures can't be turned into meaningful text, so we CROP and
SAVE each figure image and leave a [HÌNH: ...] reference in the LaTeX — the figure
is preserved, and the learnable text/math around it is captured cleanly.

API-bound (no local models) -> many workers can run concurrently; keys rotate.
"""
from __future__ import annotations
import json
import os
from PIL import Image

_PROV = None


def get_provider():
    global _PROV
    if _PROV is None:
        from src.config import load_providers
        from src.providers.base import get_active_provider
        _PROV = get_active_provider(load_providers()) or False
    return _PROV or None


_PROMPT = (
    "Bạn là công cụ OCR Toán học tiếng Việt. Chuyển ẢNH một trang tài liệu/đề Toán THCS "
    "sang LaTeX CHÍNH XÁC. CHỈ trả về JSON hợp lệ, không viết gì ngoài JSON, không dùng ```.\n"
    'Định dạng: {"latex": "<toàn trang sang LaTeX>", "figures": [{"box": [x0,y0,x1,y1]}]}\n'
    "Yêu cầu phần latex: giữ đúng dấu tiếng Việt; mọi biểu thức toán bọc trong $...$ (inline) "
    "hoặc \\[...\\] (hiển thị); giữ đúng thứ tự đọc và các ý a), b), c); MỖI HÌNH VẼ thay bằng "
    "token §FIG§ đặt đúng vị trí của nó trong văn bản.\n"
    "figures: liệt kê các vùng hình vẽ theo thứ tự xuất hiện; box là toạ độ chuẩn hoá 0..1 "
    "(trái, trên, phải, dưới). Không có hình vẽ thì figures là []."
)


def _parse_json(txt: str) -> dict:
    t = txt.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t[:4].lower() == "json":
            t = t[4:]
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        t = t[i:j + 1]
    return json.loads(t)


def page_to_latex(image_path: str, provider, figures_dir: str):
    """Returns (latex, figures) where figures = [{'bbox':[...], 'image_path':...}]."""
    raw = provider.complete(_PROMPT, images=[image_path])
    try:
        data = _parse_json(raw)
        latex = data.get("latex", "") or ""
        figs_in = data.get("figures", []) or []
    except Exception:
        return raw.strip(), []   # not JSON -> treat whole reply as latex

    saved = []
    if figs_in:
        os.makedirs(figures_dir, exist_ok=True)
        img = Image.open(image_path).convert("RGB")
        W, H = img.size
        base = os.path.splitext(os.path.basename(image_path))[0]
        for i, f in enumerate(figs_in):
            try:
                b = f.get("box") if isinstance(f, dict) else f
                x0, y0, x1, y1 = (float(v) for v in b)
                if max(x0, y0, x1, y1) <= 1.5:        # normalized -> pixels
                    x0, y0, x1, y1 = x0 * W, y0 * H, x1 * W, y1 * H
                if x1 - x0 < 5 or y1 - y0 < 5:
                    continue
                p = os.path.join(figures_dir, f"{base}_f{i}.png")
                img.crop((int(x0), int(y0), int(x1), int(y1))).save(p)
                saved.append({"bbox": [x0, y0, x1, y1], "image_path": p})
            except Exception:
                continue

    for s in saved:
        latex = latex.replace("§FIG§", f"\n[HÌNH: {os.path.basename(s['image_path'])}]\n", 1)
    latex = latex.replace("§FIG§", "\n[HÌNH VẼ]\n")
    return latex, saved
