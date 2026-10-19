"""Supervisor: keeps ingest alive — recovers orphaned jobs and auto-restarts
workers when a run is interrupted, until the queue is drained. Learns from each
session afterward (see learn.py).
"""
from __future__ import annotations
import time
from datetime import datetime, timezone
from src.orchestrator.worker import _conn, run
from src.orchestrator import queue as Q


def _utc_now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def reclaim_stale(conn, older_than=0):
    """Reset 'running' jobs whose worker died (no heartbeat) back to 'retry'."""
    cur = conn.execute(
        "UPDATE jobs SET status='retry', error='reclaimed (stale running)', "
        "updated_at=datetime('now') WHERE job_type='ingest_doc' AND status='running' "
        "AND (strftime('%s','now') - strftime('%s', updated_at)) >= ?", (older_than,))
    conn.commit()
    return cur.rowcount


def pending_count(conn):
    r = conn.execute(
        "SELECT COUNT(*) n FROM jobs WHERE job_type='ingest_doc' "
        "AND status IN ('queued','retry') AND attempts<max_attempts").fetchone()
    return r["n"] if r else 0


def supervise(db_path, renders, workers=2, restart_wait=10, learn=True, stop_flag=None):
    """Drain the ingest queue with auto-restart. stop_flag: callable()->bool to stop."""
    conn = _conn(db_path)
    start = _utc_now_str()
    cycles = 0
    while True:
        if stop_flag and stop_flag():
            print("[supervise] nhận tín hiệu dừng.")
            break
        n = reclaim_stale(conn, 0)           # between runs, no worker alive -> any 'running' is orphaned
        if n:
            print(f"[supervise] thu hồi {n} job treo -> retry", flush=True)
        pend = pending_count(conn)
        if pend == 0:
            print("[supervise] hàng đợi trống -> hoàn tất.", flush=True)
            break
        cycles += 1
        print(f"[supervise] cycle {cycles}: còn {pend} file, chạy {workers} worker...", flush=True)
        try:
            run(db_path, renders, workers, None)
        except Exception as e:
            print(f"[supervise] run lỗi: {e}", flush=True)
        if pending_count(conn) > 0 and not (stop_flag and stop_flag()):
            print(f"[supervise] bị ngắt, còn {pending_count(conn)} file — "
                  f"khởi động lại sau {restart_wait}s", flush=True)
            time.sleep(restart_wait)
    notes = None
    if learn:
        from src.orchestrator.learn import learn_from_session
        notes = learn_from_session(conn, start)
        print(f"[supervise] rút kinh nghiệm: {notes}", flush=True)
    conn.close()
    return {"cycles": cycles, "learned": notes}
