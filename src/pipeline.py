"""MVP ingest pipeline (CLI).

Usage:
  python -m src.pipeline ingest <pdf> [--grade khoi_9] [--max-ocr-pages N]

Flow: triage -> upsert document -> per page: native text extract (text pages)
or hybrid local OCR (scan/mixed pages) -> write pages + page_elements.
OCR is slow on CPU (~1-2 min/page); --max-ocr-pages caps it for quick runs
(0 = all; the rest are stored as ocr_status='pending' for a later batch).
"""
from __future__ import annotations
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymupdf  # noqa: E402
from src.config import load_settings, load_providers, abspath  # noqa: E402
from src import db  # noqa: E402
from src.ingest.triage import triage_document  # noqa: E402


def ingest(pdf_path: str, grade: str | None, max_ocr_pages: int):
    settings = load_settings()
    load_providers()
    conn = db.get_conn(abspath(settings["paths"]["db"]))
    renders = abspath(settings["paths"]["renders"])

    print(f"[triage] {os.path.basename(pdf_path)}")
    info = triage_document(pdf_path, settings, renders)
    print(f"[triage] source_type={info['source_type']} pages={info['num_pages']}")

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
            print(f"  p{p['page_no']:>3} text   ({p['text_chars']} chars)")
        else:
            if max_ocr_pages and ocr_done >= max_ocr_pages:
                db.insert_page(conn, doc_id, p["page_no"], p["kind"], p["image_path"],
                               p["text_chars"], ocr_status="pending")
                print(f"  p{p['page_no']:>3} {p['kind']:<5} deferred (cap reached)")
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
            flag = " [needs_scalpel]" if needs else ""
            print(f"  p{p['page_no']:>3} {p['kind']:<5} OCR {len(els)} elems, "
                  f"min_conf={min_conf:.2f}{flag}")
    pdf.close()
    conn.execute("UPDATE documents SET ocr_status=? WHERE id=?",
                 ("done" if not max_ocr_pages else "partial", doc_id))
    conn.commit()
    conn.close()
    print(f"[done] document id={doc_id}")


def segment_cmd(doc_id):
    settings = load_settings()
    conn = db.get_conn(abspath(settings["paths"]["db"]))
    from src.extract.segment import segment_document
    ids = [doc_id] if doc_id else [r["id"] for r in conn.execute("SELECT id FROM documents")]
    total = 0
    for d in ids:
        n = segment_document(conn, d)
        total += n
        print(f"[segment] doc {d}: {n} problems")
    conn.close()
    print(f"[segment] total {total} problems")


def dedup_cmd():
    settings = load_settings()
    conn = db.get_conn(abspath(settings["paths"]["db"]))
    from src.extract.dedup import find_duplicates
    res = find_duplicates(conn)
    conn.close()
    print(f"[dedup] problems={res['problems']} exact={res['exact_dups']} near={res['near_dups']}")


def classify_cmd():
    settings = load_settings()
    conn = db.get_conn(abspath(settings["paths"]["db"]))
    from src.classify.classifier import classify_all
    res = classify_all(conn)
    conn.close()
    print(f"[classify] problems={res['problems']} labels={res['labels']} "
          f"unclassified={res['unclassified']}")


def verify_cmd(demo: bool):
    settings = load_settings()
    conn = db.get_conn(abspath(settings["paths"]["db"]))
    if demo:
        from src.verify.runner import verify_demo
        for name, status, tool, ev in verify_demo(conn):
            print(f"  [{status:<19}] {name}")
            print(f"        tool={tool} | {ev[:90]}")
    else:
        from src.verify.runner import verify_all
        n = verify_all(conn)
        print(f"[verify] wrote {n} verification rows "
              f"(solutions carrying a verify_kind spec)")
    conn.close()


def main():
    ap = argparse.ArgumentParser(prog="mathpipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ig = sub.add_parser("ingest")
    ig.add_argument("pdf")
    ig.add_argument("--grade", default=None)
    ig.add_argument("--max-ocr-pages", type=int, default=2)
    sg = sub.add_parser("segment")
    sg.add_argument("--doc-id", type=int, default=None)   # omit = all docs
    sub.add_parser("dedup")
    sub.add_parser("classify")
    vf = sub.add_parser("verify")
    vf.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    if args.cmd == "ingest":
        ingest(args.pdf, args.grade, args.max_ocr_pages)
    elif args.cmd == "segment":
        segment_cmd(args.doc_id)
    elif args.cmd == "dedup":
        dedup_cmd()
    elif args.cmd == "classify":
        classify_cmd()
    elif args.cmd == "verify":
        verify_cmd(args.demo)


if __name__ == "__main__":
    main()
