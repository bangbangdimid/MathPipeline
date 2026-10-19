"""Learn from an ingest session: summarise what happened and distil 'ops' rules.

These ops rules accumulate across sessions (e.g. recurring file errors, heavy
needs_scalpel load). They are kept OUT of the compose prompt (rules_for_prompt
only injects style/math/pedagogy) so operational lessons don't pollute content.
"""
from __future__ import annotations
from src.feedback.store import record_feedback, add_rule


def learn_from_session(conn, session_start):
    def one(q, *p):
        r = conn.execute(q, p).fetchone()
        return r["n"] if r else 0

    done = one("SELECT COUNT(*) n FROM jobs WHERE job_type='ingest_doc' AND status='done' "
               "AND updated_at>=?", session_start)
    failed = one("SELECT COUNT(*) n FROM jobs WHERE job_type='ingest_doc' AND status='failed' "
                 "AND updated_at>=?", session_start)
    retried = one("SELECT COUNT(*) n FROM jobs WHERE job_type='ingest_doc' AND attempts>1 "
                  "AND updated_at>=?", session_start)
    needs = one("SELECT COUNT(*) n FROM page_elements WHERE needs_scalpel=1")
    top_errs = conn.execute(
        "SELECT substr(error,1,120) e, COUNT(*) n FROM jobs WHERE job_type='ingest_doc' "
        "AND status IN ('failed','retry') AND error IS NOT NULL AND updated_at>=? "
        "GROUP BY e ORDER BY n DESC LIMIT 3", (session_start,)).fetchall()

    summary = (f"Phiên ingest: done={done}, failed={failed}, retried={retried}, "
               f"needs_scalpel(tổng)={needs}")
    fid = record_feedback(conn, "session", 0, "approve", reason=summary, editor="supervisor")

    rules_added = 0
    for r in top_errs:
        if r["n"] >= 3 and r["e"]:
            add_rule(conn, f"Lỗi ingest lặp {r['n']} lần: “{r['e']}” — xem lại/bỏ qua file dạng này",
                     "ops", derived_from=fid)
            rules_added += 1
    if failed >= 5:
        add_rule(conn, f"Phiên có {failed} file lỗi — cân nhắc kiểm tra định dạng nguồn / tăng retry",
                 "ops", derived_from=fid)
        rules_added += 1
    if needs >= 20:
        add_rule(conn, f"Tồn {needs} trang cần scalpel — nên chạy Scalpel (Gemini) trước khi dựng ngân hàng",
                 "ops", derived_from=fid)
        rules_added += 1

    return {"done": done, "failed": failed, "retried": retried,
            "needs_scalpel": needs, "rules_added": rules_added}
