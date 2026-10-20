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
    conn = db.get_conn(abspath(settings["paths"]["db"]))
    renders = abspath(settings["paths"]["renders"])
    from src.ingest.run import ingest_document
    print(f"[ingest] {os.path.basename(pdf_path)}")
    doc_id = ingest_document(conn, pdf_path, grade, max_ocr_pages, settings, renders,
                             log=lambda m: print("  " + m))
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


def orchestrate_cmd(args):
    settings = load_settings()
    db_path = abspath(settings["paths"]["db"])
    renders = abspath(settings["paths"]["renders"])
    from src.orchestrator import queue as Q
    if args.sub == "enqueue":
        conn = db.get_conn(db_path)
        added, total = Q.enqueue_dir(conn, args.dir, args.grade, args.limit, args.max_ocr_pages)
        print(f"[orchestrate] enqueued {added} new ingest_doc jobs (of {total} pdfs found)")
        conn.close()
    elif args.sub == "status":
        conn = db.get_conn(db_path)
        print("[orchestrate] ingest_doc jobs:", Q.status(conn))
        conn.close()
    elif args.sub == "run":
        from src.orchestrator.worker import run
        print(f"[orchestrate] running {args.workers} worker(s)"
              + (f", max {args.max} jobs each" if args.max else ""))
        run(db_path, renders, args.workers, args.max)
        conn = db.get_conn(db_path)
        print("[orchestrate] after run:", Q.status(conn))
        conn.close()
    elif args.sub == "requeue-failed":
        conn = db.get_conn(db_path)
        n = Q.requeue_failed(conn)
        conn.close()
        print(f"[orchestrate] requeued {n} failed jobs")
    elif args.sub == "supervise":
        from src.orchestrator.supervisor import supervise
        print(f"[orchestrate] supervise: {args.workers} worker(s), tự khởi động lại "
              f"sau {args.wait}s khi bị ngắt")
        supervise(db_path, renders, args.workers, args.wait)


def dashboard_cmd(host, port):
    import uvicorn
    from src.dashboard.app import app as dash_app
    print(f"[dashboard] http://{host}:{port}  (Ctrl+C để dừng)")
    uvicorn.run(dash_app, host=host, port=port, log_level="warning")


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
    ds = sub.add_parser("dashboard")
    ds.add_argument("--host", default="127.0.0.1")
    ds.add_argument("--port", type=int, default=8000)
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
    orc = sub.add_parser("orchestrate")
    osub = orc.add_subparsers(dest="sub", required=True)
    oe = osub.add_parser("enqueue")
    oe.add_argument("--dir", required=True)
    oe.add_argument("--grade", default=None)
    oe.add_argument("--limit", type=int, default=None)
    oe.add_argument("--max-ocr-pages", type=int, default=0)
    osub.add_parser("status")
    osub.add_parser("requeue-failed")
    orun = osub.add_parser("run")
    orun.add_argument("--workers", type=int, default=2)
    orun.add_argument("--max", type=int, default=None)
    osup = osub.add_parser("supervise")
    osup.add_argument("--workers", type=int, default=2)
    osup.add_argument("--wait", type=int, default=10)
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
    elif args.cmd == "dashboard":
        dashboard_cmd(args.host, args.port)
    elif args.cmd == "orchestrate":
        orchestrate_cmd(args)
    elif args.cmd == "feedback":
        feedback_cmd(args)
    elif args.cmd == "rules":
        rules_cmd(args.category, args.prompt)


if __name__ == "__main__":
    main()
