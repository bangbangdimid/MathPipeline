"""Reusable single-document ingest (used by the CLI and the orchestrator worker)."""
from __future__ import annotations
import pymupdf
from src import db
from src.ingest.triage import triage_document


def ingest_document(conn, pdf_path, grade, max_ocr_pages, settings, renders_dir,
                    log=lambda *_: None):
    """Triage -> upsert document -> per-page native text or hybrid OCR. Returns doc_id."""
    info = triage_document(pdf_path, settings, renders_dir)
    log(f"source_type={info['source_type']} pages={info['num_pages']}")
    doc_id = db.upsert_document(conn, sha256=info["sha256"], pdf_path=pdf_path,
                                grade=grade, num_pages=info["num_pages"],
                                source_type=info["source_type"], bytes=info["bytes"])
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
