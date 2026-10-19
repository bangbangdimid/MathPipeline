"""Re-propagate scalpel/OCR fixes downstream.

After the Gemini scalpel (or any edit) changes page_elements, the derived layers
(pages.latex -> problems) are stale. This rebuilds pages.latex from the current
elements, re-segments problems (clearing the document's old problems + dependents),
then re-classifies and re-dedups. Native text pages (no elements) are untouched.
"""
from __future__ import annotations
from src.ingest.ocr_hybrid import elements_to_latex


def rebuild_pages(conn, doc_id=None) -> int:
    if doc_id:
        rows = conn.execute(
            "SELECT DISTINCT pe.page_id FROM page_elements pe JOIN pages p ON p.id=pe.page_id "
            "WHERE p.document_id=?", (doc_id,)).fetchall()
    else:
        rows = conn.execute("SELECT DISTINCT page_id FROM page_elements").fetchall()
    for r in rows:
        pid = r["page_id"]
        els = conn.execute(
            "SELECT el_type,content FROM page_elements WHERE page_id=? ORDER BY idx", (pid,)
        ).fetchall()
        items = [dict(el_type=e["el_type"], content=e["content"]) for e in els]
        conn.execute("UPDATE pages SET latex=? WHERE id=?", (elements_to_latex(items), pid))
    conn.commit()
    return len(rows)


def _clear_document_problems(conn, doc_id):
    pids = [r["problem_id"] for r in
            conn.execute("SELECT problem_id FROM provenance WHERE document_id=?", (doc_id,))]
    if pids:
        qs = ",".join("?" * len(pids))
        conn.execute(f"DELETE FROM classifications WHERE problem_id IN ({qs})", pids)
        conn.execute(f"DELETE FROM difficulty WHERE problem_id IN ({qs})", pids)
        conn.execute(f"DELETE FROM solutions WHERE problem_id IN ({qs})", pids)
        conn.execute(f"DELETE FROM duplicates WHERE problem_a IN ({qs}) OR problem_b IN ({qs})",
                     pids + pids)
        conn.execute(f"DELETE FROM chuyen_de_problems WHERE problem_id IN ({qs})", pids)
        conn.execute("DELETE FROM provenance WHERE document_id=?", (doc_id,))
        conn.execute(f"DELETE FROM problems WHERE id IN ({qs})", pids)
    conn.commit()


def repropagate(conn, doc_id=None, reclassify=True, redup=True) -> dict:
    from src.extract.segment import segment_document
    n_pages = rebuild_pages(conn, doc_id)
    docs = [doc_id] if doc_id else [r["id"] for r in conn.execute("SELECT id FROM documents")]
    n_problems = 0
    for d in docs:
        _clear_document_problems(conn, d)
        n_problems += segment_document(conn, d)
    if reclassify:
        from src.classify.classifier import classify_all
        classify_all(conn)
    if redup:
        from src.extract.dedup import find_duplicates
        find_duplicates(conn)
    return {"pages_rebuilt": n_pages, "problems": n_problems}
