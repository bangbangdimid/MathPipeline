"""Triage: hash a PDF, classify born-digital vs scan, render scan pages."""
from __future__ import annotations
import hashlib
import os
import pymupdf


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def classify_page_kind(page, text_min, scan_max) -> tuple[str, int]:
    txt = page.get_text("text")
    n = len(txt)
    if n >= text_min:
        return "text", n
    if n <= scan_max:
        return "scan", n
    return "mixed", n


def render_page(page, dpi, out_path) -> str:
    mat = pymupdf.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    pix.save(out_path)
    return out_path


def triage_document(pdf_path: str, settings: dict, renders_dir: str):
    """Returns dict with doc-level + per-page triage info (no DB writes)."""
    ocr = settings["ocr"]
    sha = sha256_of(pdf_path)
    doc = pymupdf.open(pdf_path)
    kinds = []
    pages = []
    os.makedirs(renders_dir, exist_ok=True)
    for i in range(doc.page_count):
        page = doc[i]
        kind, nchars = classify_page_kind(page, ocr["text_layer_min_cpp"], ocr["scan_max_cpp"])
        img_path = None
        if kind in ("scan", "mixed"):
            img_path = os.path.join(renders_dir, f"{sha[:12]}_p{i+1:03d}.png")
            render_page(page, ocr["render_dpi"], img_path)
        pages.append(dict(page_no=i + 1, kind=kind, text_chars=nchars, image_path=img_path))
        kinds.append(kind)
    doc.close()
    text_ratio = kinds.count("text") / max(len(kinds), 1)
    source_type = "text" if text_ratio > 0.8 else ("scan" if text_ratio < 0.2 else "mixed")
    return dict(sha256=sha, pdf_path=pdf_path, num_pages=len(pages),
                bytes=os.path.getsize(pdf_path), source_type=source_type, pages=pages)
