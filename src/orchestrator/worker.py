"""Multiprocessing workers that drain the ingest job queue.

OCR is CPU-bound (local pix2text/easyocr), so we use processes, not asyncio.
Each worker loads its own OCR models (~2-3 GB RAM each) — keep --workers small.
WAL + busy_timeout let the processes share one SQLite DB safely.
"""
from __future__ import annotations
import json
import multiprocessing as mp
import sqlite3
from src.config import load_settings, abspath
from src.orchestrator import queue as Q


def _conn(db_path):
    c = sqlite3.connect(db_path, timeout=60, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=60000")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def worker_loop(db_path, renders, worker_id, max_jobs):
    from src.ingest.run import ingest_document
    settings = load_settings()
    conn = _conn(db_path)
    done = 0
    while True:
        if max_jobs and done >= max_jobs:
            break
        job = Q.claim_job(conn)
        if not job:
            break
        ck = json.loads(job["checkpoint"])
        try:
            did = ingest_document(conn, ck["pdf"], ck.get("grade"),
                                  ck.get("max_ocr_pages", 0), settings, renders)
            Q.complete_job(conn, job["id"], did)
            print(f"[w{worker_id}] job{job['id']} -> doc{did}", flush=True)
        except Exception as e:
            msg = str(e)
            # FK/UNIQUE errors are deterministic (duplicate-file race) -> don't retry
            # them forever (they block the queue); mark failed immediately.
            permanent = ("FOREIGN KEY" in msg or "IntegrityError" in msg
                         or "UNIQUE constraint" in msg)
            Q.fail_job(conn, job["id"], repr(e), permanent=permanent)
            tag = " (permanent)" if permanent else ""
            print(f"[w{worker_id}] job{job['id']} FAILED{tag}: {e}", flush=True)
        done += 1
    conn.close()
    print(f"[w{worker_id}] exit ({done} jobs this run)", flush=True)


def run(db_path, renders, n_workers=2, max_jobs=None):
    # ensure WAL is set once before spawning
    c = _conn(db_path); c.close()
    procs = [mp.Process(target=worker_loop, args=(db_path, renders, i + 1, max_jobs))
             for i in range(n_workers)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
