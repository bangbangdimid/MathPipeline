"""SQLite-backed job queue for crash-safe, resumable, parallel ingest.

Each ingest_doc job carries its pdf path + options in `checkpoint` (JSON).
Workers claim one job atomically (BEGIN IMMEDIATE), so several processes can
share one DB without grabbing the same job. Re-running `run` resumes whatever is
still queued/retry — nothing is reprocessed because done jobs stay 'done'.
"""
from __future__ import annotations
import json
import os


def _ck(pdf, grade, max_ocr_pages):
    return json.dumps({"pdf": pdf, "grade": grade, "max_ocr_pages": max_ocr_pages},
                      ensure_ascii=False, sort_keys=True)


def enqueue_dir(conn, root, grade=None, limit=None, max_ocr_pages=0):
    pdfs = []
    for dp, _, fs in os.walk(root):
        for f in fs:
            if f.lower().endswith(".pdf"):
                pdfs.append(os.path.join(dp, f))
    pdfs.sort()
    if limit:
        pdfs = pdfs[:limit]
    added = 0
    for p in pdfs:
        ck = _ck(p, grade, max_ocr_pages)
        if conn.execute("SELECT 1 FROM jobs WHERE job_type='ingest_doc' AND checkpoint=?",
                        (ck,)).fetchone():
            continue  # already enqueued/processed
        conn.execute(
            "INSERT INTO jobs (job_type,target_type,status,checkpoint) "
            "VALUES ('ingest_doc','document','queued',?)", (ck,))
        added += 1
    conn.commit()
    return added, len(pdfs)


def claim_job(conn):
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(
        "SELECT id,checkpoint FROM jobs WHERE job_type='ingest_doc' "
        "AND status IN ('queued','retry') AND attempts<max_attempts ORDER BY id LIMIT 1"
    ).fetchone()
    if not row:
        conn.execute("COMMIT")
        return None
    conn.execute("UPDATE jobs SET status='running', attempts=attempts+1, "
                 "updated_at=datetime('now') WHERE id=?", (row["id"],))
    conn.execute("COMMIT")
    return {"id": row["id"], "checkpoint": row["checkpoint"]}


def complete_job(conn, jid, target_id=None):
    conn.execute("UPDATE jobs SET status='done', target_id=COALESCE(?,target_id), "
                 "updated_at=datetime('now') WHERE id=?", (target_id, jid))
    conn.commit()


def fail_job(conn, jid, err):
    r = conn.execute("SELECT attempts,max_attempts FROM jobs WHERE id=?", (jid,)).fetchone()
    status = "retry" if r["attempts"] < r["max_attempts"] else "failed"
    conn.execute("UPDATE jobs SET status=?, error=?, updated_at=datetime('now') WHERE id=?",
                 (status, str(err)[:500], jid))
    conn.commit()


def status(conn):
    return {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) n FROM jobs WHERE job_type='ingest_doc' GROUP BY status")}


def requeue_failed(conn, bump_attempts=8):
    """Reset failed ingest jobs back to the queue (fresh attempts) to run again."""
    cur = conn.execute(
        "UPDATE jobs SET status='queued', attempts=0, max_attempts=?, error=NULL "
        "WHERE job_type='ingest_doc' AND status='failed'", (bump_attempts,))
    conn.commit()
    return cur.rowcount
