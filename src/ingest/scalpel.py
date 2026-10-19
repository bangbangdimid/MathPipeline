"""Gemini scalpel: whole-element structured OCR for blocks the local hybrid
cannot stitch reliably (dense inline formulas, low confidence, stylized headers).

This is the ONLY place cloud quota is spent on OCR. Activates only when a
GEMINI_API_KEY is present; otherwise it is a no-op with a clear message.
"""
from __future__ import annotations
import json
import os
import tempfile
from PIL import Image


class ScalpelNotConfigured(RuntimeError):
    pass


def scalpel_element(image_crop_path: str, provider=None) -> str:
    if provider is None:
        raise ScalpelNotConfigured(
            "Scalpel needs a provider. Set GEMINI_API_KEY and pass a provider from "
            "src.providers.base.get_active_provider().")
    return provider.vlm_to_latex(image_crop_path)


def rescan_scalpel(conn, provider, limit=None) -> dict:
    """Re-OCR every page_element flagged needs_scalpel using the provider.

    Crops the element bbox from its page render, sends to the VLM, and replaces
    content (source_engine='gemini', needs_scalpel=0). Returns counts.
    """
    if provider is None:
        return {"error": "no provider (GEMINI_API_KEY not set)", "updated": 0, "pending": 0}
    rows = conn.execute(
        """SELECT pe.id, pe.bbox, p.image_path
           FROM page_elements pe JOIN pages p ON p.id = pe.page_id
           WHERE pe.needs_scalpel = 1 AND p.image_path IS NOT NULL""").fetchall()
    total_pending = len(rows)
    if limit:
        rows = rows[:limit]
    updated, failed = 0, 0
    tmpdir = tempfile.mkdtemp(prefix="scalpel_")
    for r in rows:
        try:
            box = json.loads(r["bbox"])
            crop = Image.open(r["image_path"]).crop(tuple(box)).convert("RGB")
            tmp = os.path.join(tmpdir, f"el_{r['id']}.png")
            crop.save(tmp)
            latex = scalpel_element(tmp, provider)
            conn.execute(
                "UPDATE page_elements SET content=?, source_engine='gemini', needs_scalpel=0 "
                "WHERE id=?", (latex, r["id"]))
            updated += 1
        except Exception as e:
            conn.execute(
                "UPDATE page_elements SET needs_scalpel=2 WHERE id=?", (r["id"],))  # 2 = scalpel_failed
            failed += 1
            print(f"  scalpel element {r['id']} failed: {e}")
    conn.commit()
    return {"pending": total_pending, "updated": updated, "failed": failed}
