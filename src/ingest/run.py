"""Reusable single-document ingest (used by the CLI and the orchestrator worker).

Two phases: (1) OCR every page into memory holding NO DB lock (the slow Gemini
calls happen here, fully parallel across workers); (2) write all rows for the doc
in ONE short IMMEDIATE transaction. Phase 2 being atomic is what makes concurrent
duplicate-file jobs serialize cleanly instead of racing into FOREIGN KEY errors.
"""
from __future__ import annotations
import json
import os
import random
import time
import pymupdf
from src.ingest.triage import triage_document


def _ocr_pages(pdf_path, info, engine, max_ocr_pages, figures_dir, log):
    """Phase 1 — OCR into memory. Returns list of page dicts (with elements/figures)."""
    out = []
    pdf = pymupdf.open(pdf_path)
    ocr_done = 0
    for p in info["pages"]:
        base = dict(page_no=p["page_no"], kind=p["kind"], image_path=p.get("image_path"),
                    text_chars=p["text_chars"], ocr_engine=None, ocr_status="pending",
                    latex=None, min_conf=None, needs_review=0, elements=[], figures=[])
        if p["kind"] == "text":
            base.update(image_path=None, ocr_engine="native", ocr_status="local_done",
                        latex=pdf[p["page_no"] - 1].get_text("text"), min_conf=1.0)
        elif max_ocr_pages and ocr_done >= max_ocr_pages:
            pass  # leave as pending
        else:
            prov = None
            if engine == "gemini":
                from src.ingest.ocr_gemini import get_provider
                prov = get_provider()
            if engine == "gemini" and prov is not None:
                from src.ingest.ocr_gemini import page_to_latex
                latex, figs = page_to_latex(p["image_path"], prov, figures_dir)
                base.update(ocr_engine="gemini", ocr_status="local_done", latex=latex,
                            min_conf=0.95,
                            elements=[dict(idx=0, el_type="text", bbox=[0, 0, 0, 0],
                                           content=latex, source_engine="gemini",
                                           confidence=0.95, needs_scalpel=0)],
                            figures=figs)
                ocr_done += 1
                log(f"p{p['page_no']} GEMINI ({len(figs)} hình)")
            else:
                from src.ingest.ocr_hybrid import page_to_elements, elements_to_latex
                els = page_to_elements(p["image_path"])
                needs = 1 if any(e["needs_scalpel"] for e in els) else 0
                base.update(ocr_engine="pix2text+easyocr",
                            ocr_status=("needs_review" if needs else "local_done"),
                            latex=elements_to_latex(els),
                            min_conf=min([e["confidence"] for e in els], default=1.0),
                            needs_review=needs, elements=els)
                ocr_done += 1
        out.append(base)
    pdf.close()
    return out


def _write_doc(conn, info, pdf_path, grade, max_ocr_pages, pages_out):
    """Phase 2 — atomic write. Returns doc_id."""
    sha = info["sha256"]
    prev_iso = conn.isolation_level
    conn.isolation_level = None               # explicit transaction control
    try:
        for attempt in range(6):
            try:
                conn.execute("BEGIN IMMEDIATE")
                cur = conn.execute(
                    "INSERT OR IGNORE INTO documents (sha256,pdf_path,grade,num_pages,"
                    "source_type,bytes) VALUES (?,?,?,?,?,?)",
                    (sha, pdf_path, grade, info["num_pages"], info["source_type"], info["bytes"]))
                if cur.lastrowid:
                    doc_id = cur.lastrowid
                else:
                    doc_id = conn.execute("SELECT id FROM documents WHERE sha256=?",
                                          (sha,)).fetchone()[0]
                old = [r[0] for r in conn.execute(
                    "SELECT id FROM pages WHERE document_id=?", (doc_id,))]
                if old:
                    qs = ",".join("?" * len(old))
                    conn.execute(f"DELETE FROM page_elements WHERE page_id IN ({qs})", old)
                    conn.execute(f"DELETE FROM figures WHERE page_id IN ({qs})", old)
                    conn.execute("DELETE FROM pages WHERE document_id=?", (doc_id,))
                for pg in pages_out:
                    pc = conn.execute(
                        "INSERT INTO pages (document_id,page_no,kind,image_path,text_chars,"
                        "ocr_engine,ocr_status,latex,min_conf,needs_review,processed_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                        (doc_id, pg["page_no"], pg["kind"], pg["image_path"], pg["text_chars"],
                         pg["ocr_engine"], pg["ocr_status"], pg["latex"], pg["min_conf"],
                         pg["needs_review"]))
                    page_id = pc.lastrowid
                    for e in pg["elements"]:
                        conn.execute(
                            "INSERT INTO page_elements (page_id,idx,el_type,bbox,content,"
                            "source_engine,confidence,needs_scalpel) VALUES (?,?,?,?,?,?,?,?)",
                            (page_id, e["idx"], e["el_type"], json.dumps(e["bbox"]),
                             e["content"], e["source_engine"], e["confidence"], e["needs_scalpel"]))
                    for f in pg["figures"]:
                        conn.execute(
                            "INSERT INTO figures (page_id,bbox,image_path,recon_status) "
                            "VALUES (?,?,?, 'none')",
                            (page_id, json.dumps(f["bbox"]), f["image_path"]))
                conn.execute("UPDATE documents SET ocr_status=? WHERE id=?",
                             ("done" if not max_ocr_pages else "partial", doc_id))
                conn.execute("COMMIT")
                return doc_id
            except Exception as e:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                if ("locked" in str(e) or "busy" in str(e)) and attempt < 5:
                    time.sleep(0.3 + random.random() * 0.7)
                    continue
                raise
    finally:
        conn.isolation_level = prev_iso


def ingest_document(conn, pdf_path, grade, max_ocr_pages, settings, renders_dir,
                    log=lambda *_: None):
    """Triage -> OCR (phase 1) -> atomic write (phase 2). Returns doc_id."""
    engine = (settings.get("ocr", {}) or {}).get("engine", "local")
    figures_dir = os.path.join(os.path.dirname(renders_dir), "figures")
    info = triage_document(pdf_path, settings, renders_dir)
    log(f"source_type={info['source_type']} pages={info['num_pages']}")
    pages_out = _ocr_pages(pdf_path, info, engine, max_ocr_pages, figures_dir, log)
    return _write_doc(conn, info, pdf_path, grade, max_ocr_pages, pages_out)
