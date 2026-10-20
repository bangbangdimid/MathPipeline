"""Reusable single-document ingest (used by the CLI and the orchestrator worker)."""
from __future__ import annotations
import json
import os
import pymupdf
from src import db
from src.ingest.triage import triage_document


def ingest_document(conn, pdf_path, grade, max_ocr_pages, settings, renders_dir,
                    log=lambda *_: None):
    """Triage -> upsert document -> per-page native text or OCR (local|gemini). Returns doc_id."""
    engine = (settings.get("ocr", {}) or {}).get("engine", "local")
    figures_dir = os.path.join(os.path.dirname(renders_dir), "figures")
    info = triage_document(pdf_path, settings, renders_dir)
    log(f"source_type={info['source_type']} pages={info['num_pages']}")
    doc_id = db.upsert_document(conn, sha256=info["sha256"], pdf_path=pdf_path,
                                grade=grade, num_pages=info["num_pages"],
                                source_type=info["source_type"], bytes=info["bytes"])
    conn.commit()

    # Clean re-ingest: drop prior pages/elements/figures of this doc so a retry or a
    # duplicate file doesn't orphan child rows (avoids INSERT OR REPLACE FK failures).
    old = [r["id"] for r in conn.execute("SELECT id FROM pages WHERE document_id=?", (doc_id,))]
    if old:
        qs = ",".join("?" * len(old))
        conn.execute(f"DELETE FROM page_elements WHERE page_id IN ({qs})", old)
        conn.execute(f"DELETE FROM figures WHERE page_id IN ({qs})", old)
        conn.execute("DELETE FROM pages WHERE document_id=?", (doc_id,))
        conn.commit()

    pdf = pymupdf.open(pdf_path)
    ocr_done = 0
    for p in info["pages"]:
        if p["kind"] == "text":
            text = pdf[p["page_no"] - 1].get_text("text")
            db.insert_page(conn, doc_id, p["page_no"], "text", None, p["text_chars"],
                           ocr_engine="native", ocr_status="local_done",
                           latex=text, min_conf=1.0)
        else:
            if max_ocr_pages and ocr_done >= max_ocr_pages:
                db.insert_page(conn, doc_id, p["page_no"], p["kind"], p["image_path"],
                               p["text_chars"], ocr_status="pending")
                continue
            prov = None
            if engine == "gemini":
                from src.ingest.ocr_gemini import get_provider
                prov = get_provider()
            if engine == "gemini" and prov is not None:
                from src.ingest.ocr_gemini import page_to_latex
                latex, figs = page_to_latex(p["image_path"], prov, figures_dir)
                page_id = db.insert_page(conn, doc_id, p["page_no"], p["kind"], p["image_path"],
                                         p["text_chars"], ocr_engine="gemini",
                                         ocr_status="local_done", latex=latex,
                                         min_conf=0.95, needs_review=0)
                db.insert_element(conn, page_id, dict(
                    idx=0, el_type="text", bbox=[0, 0, 0, 0], content=latex,
                    source_engine="gemini", confidence=0.95, needs_scalpel=0))
                for f in figs:
                    conn.execute("INSERT INTO figures (page_id,bbox,image_path,recon_status) "
                                 "VALUES (?,?,?, 'none')",
                                 (page_id, json.dumps(f["bbox"]), f["image_path"]))
                conn.commit()
                ocr_done += 1
                log(f"p{p['page_no']} GEMINI ({len(figs)} hình)")
            else:
                from src.ingest.ocr_hybrid import page_to_elements, elements_to_latex
                els = page_to_elements(p["image_path"])
                latex = elements_to_latex(els)
                min_conf = min([e["confidence"] for e in els], default=1.0)
                needs = 1 if any(e["needs_scalpel"] for e in els) else 0
                page_id = db.insert_page(conn, doc_id, p["page_no"], p["kind"], p["image_path"],
                                         p["text_chars"], ocr_engine="pix2text+easyocr",
                                         ocr_status=("needs_review" if needs else "local_done"),
                                         latex=latex, min_conf=min_conf, needs_review=needs)
                for e in els:
                    db.insert_element(conn, page_id, e)
                ocr_done += 1
                log(f"p{p['page_no']} OCR {len(els)} elems min_conf={min_conf:.2f}"
                    + (" [needs_scalpel]" if needs else ""))
    pdf.close()
    conn.execute("UPDATE documents SET ocr_status=? WHERE id=?",
                 ("done" if not max_ocr_pages else "partial", doc_id))
    conn.commit()
    return doc_id
