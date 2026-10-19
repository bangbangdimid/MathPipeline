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


def compose_cmd(title, grade, label, difficulty, limit, use_llm=False):
    settings = load_settings()
    conn = db.get_conn(abspath(settings["paths"]["db"]))
    xelatex = settings.get("tools", {}).get("xelatex", "xelatex")
    out_dir = abspath(settings["paths"].get("chuyende", "data/chuyende"))
    provider = None
    if use_llm:
        from src.providers.base import get_active_provider
        provider = get_active_provider(load_providers())
        if provider is None:
            print("[compose] --use-llm requested but GEMINI_API_KEY not set; "
                  "using template prose.")
    from src.compose.builder import compose
    res, err = compose(conn, title, grade, label, difficulty, limit, xelatex, out_dir,
                       provider=provider)
    conn.close()
    if err:
        print(f"[compose] {err}")
        return
    print(f"[compose] chuyen_de id={res['chuyen_de_id']} problems={res['problems']} "
          f"compile_rc={res['compile_rc']}")
    print(f"          tex={res['tex']}")
    print(f"          pdf={res['pdf']}")


def repropagate_cmd(doc_id):
    settings = load_settings()
    conn = db.get_conn(abspath(settings["paths"]["db"]))
    from src.ingest.repropagate import repropagate
    res = repropagate(conn, doc_id)
    conn.close()
    print(f"[repropagate] pages_rebuilt={res['pages_rebuilt']} problems={res['problems']} "
          f"(re-classified + re-deduped)")


def scalpel_cmd(limit):
    settings = load_settings()
    conn = db.get_conn(abspath(settings["paths"]["db"]))
    from src.providers.base import get_active_provider
    from src.ingest.scalpel import rescan_scalpel
    provider = get_active_provider(load_providers())
    if provider is None:
        print("[scalpel] No Gemini keys configured — add them to config/gemini_keys.txt "
              "(one per line) or set GEMINI_API_KEYS. No elements changed.")
        pending = conn.execute(
            "SELECT COUNT(*) c FROM page_elements WHERE needs_scalpel=1").fetchone()["c"]
        print(f"[scalpel] {pending} elements are flagged needs_scalpel and waiting.")
        conn.close()
        return
    res = rescan_scalpel(conn, provider, limit)
    conn.close()
    print(f"[scalpel] pending={res.get('pending')} updated={res.get('updated')} "
          f"failed={res.get('failed')}")


def feedback_cmd(args):
    settings = load_settings()
    conn = db.get_conn(abspath(settings["paths"]["db"]))
    from src.feedback import store
    fid = store.record_feedback(conn, args.target, args.id, args.action,
                                reason=args.reason, before=args.before,
                                after=args.after, editor=args.editor)
    print(f"[feedback] recorded id={fid} ({args.action} on {args.target} {args.id})")
    rule_text = args.rule or (args.reason if args.action in store.CORRECTION_ACTIONS else None)
    if rule_text:
        rid, cat = store.add_rule(conn, rule_text, args.category, derived_from=fid)
        print(f"[feedback] distilled rule id={rid} [{cat}]: {rule_text}")
    conn.close()


def rules_cmd(category, as_prompt):
    settings = load_settings()
    conn = db.get_conn(abspath(settings["paths"]["db"]))
    from src.feedback import store
    if as_prompt:
        print(store.rules_for_prompt(conn, category) or "(no active rules)")
    else:
        rows = store.active_rules(conn, category)
        if not rows:
            print("(no active rules)")
        for r in rows:
            print(f"  #{r['id']} [{r['category']}] {r['rule_text']}")
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
    co = sub.add_parser("compose")
    co.add_argument("--title", required=True)
    co.add_argument("--grade", default=None)
    co.add_argument("--label", default=None)
    co.add_argument("--difficulty", type=int, default=None)
    co.add_argument("--limit", type=int, default=None)
    co.add_argument("--use-llm", action="store_true", help="generate intro prose via Gemini")
    sc = sub.add_parser("scalpel")
    sc.add_argument("--limit", type=int, default=None)
    rp = sub.add_parser("repropagate")
    rp.add_argument("--doc-id", type=int, default=None)
    fb = sub.add_parser("feedback")
    fb.add_argument("--target", required=True, choices=["problem", "solution", "chuyen_de"])
    fb.add_argument("--id", type=int, required=True)
    fb.add_argument("--action", required=True,
                    choices=["approve", "reject", "edit", "rewrite"])
    fb.add_argument("--reason", default=None)
    fb.add_argument("--before", default=None)
    fb.add_argument("--after", default=None)
    fb.add_argument("--rule", default=None, help="explicit rule to distill")
    fb.add_argument("--category", default=None, choices=["style", "math", "pedagogy"])
    fb.add_argument("--editor", default="human")
    rl = sub.add_parser("rules")
    rl.add_argument("--category", default=None, choices=["style", "math", "pedagogy"])
    rl.add_argument("--prompt", action="store_true", help="print prompt-ready block")
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
    elif args.cmd == "compose":
        compose_cmd(args.title, args.grade, args.label, args.difficulty, args.limit,
                    use_llm=args.use_llm)
    elif args.cmd == "scalpel":
        scalpel_cmd(args.limit)
    elif args.cmd == "repropagate":
        repropagate_cmd(args.doc_id)
    elif args.cmd == "feedback":
        feedback_cmd(args)
    elif args.cmd == "rules":
        rules_cmd(args.category, args.prompt)


if __name__ == "__main__":
    main()
