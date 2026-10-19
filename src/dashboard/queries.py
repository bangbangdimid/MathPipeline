"""Read helpers for the dashboard."""
from __future__ import annotations


def stats(conn):
    one = lambda q: conn.execute(q).fetchone()[0]
    return {
        "documents": one("SELECT COUNT(*) FROM documents"),
        "pages": one("SELECT COUNT(*) FROM pages"),
        "pages_by_status": {r["ocr_status"]: r["n"] for r in conn.execute(
            "SELECT ocr_status, COUNT(*) n FROM pages GROUP BY ocr_status")},
        "needs_scalpel": one("SELECT COUNT(*) FROM page_elements WHERE needs_scalpel=1"),
        "problems": one("SELECT COUNT(*) FROM problems"),
        "flagged": one("SELECT COUNT(*) FROM problems WHERE status='flagged'"),
        "classified": one("SELECT COUNT(DISTINCT problem_id) FROM classifications"),
        "duplicates": one("SELECT COUNT(*) FROM duplicates"),
        "chuyende": one("SELECT COUNT(*) FROM chuyen_de"),
        "rules": one("SELECT COUNT(*) FROM feedback_rules WHERE active=1"),
        "verify_by_status": {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) n FROM verification GROUP BY status")},
    }


def tier1_labels(conn):
    return [r["label"] for r in conn.execute(
        "SELECT DISTINCT label FROM classifications WHERE tier=1 ORDER BY label")]


def list_problems(conn, grade=None, label=None):
    q = "SELECT DISTINCT p.id, p.grade_level, p.status, substr(p.statement_latex,1,90) s FROM problems p"
    where, params = [], []
    if label:
        q += " JOIN classifications c ON c.problem_id=p.id"
        where.append("c.label=?"); params.append(label)
    if grade:
        where.append("p.grade_level=?"); params.append(grade)
    if where:
        q += " WHERE " + " AND ".join(where)
    return conn.execute(q + " ORDER BY p.id", params).fetchall()


def get_problem(conn, pid):
    p = conn.execute("SELECT * FROM problems WHERE id=?", (pid,)).fetchone()
    labels = conn.execute(
        "SELECT tier,label,confidence FROM classifications WHERE problem_id=? ORDER BY tier,confidence DESC",
        (pid,)).fetchall()
    prov = conn.execute(
        "SELECT d.pdf_path, pr.page_no FROM provenance pr JOIN documents d ON d.id=pr.document_id "
        "WHERE pr.problem_id=?", (pid,)).fetchall()
    return p, labels, prov


def list_chuyende(conn):
    return conn.execute(
        "SELECT cd.id, cd.title, cd.grade, cd.status, v.pdf_path "
        "FROM chuyen_de cd LEFT JOIN chuyen_de_versions v "
        "ON v.chuyen_de_id=cd.id AND v.version=cd.current_version ORDER BY cd.id").fetchall()


def get_chuyende(conn, cid):
    cd = conn.execute("SELECT * FROM chuyen_de WHERE id=?", (cid,)).fetchone()
    members = conn.execute(
        "SELECT cp.position, cp.role, p.id, substr(p.statement_latex,1,90) s "
        "FROM chuyen_de_problems cp JOIN problems p ON p.id=cp.problem_id "
        "WHERE cp.chuyen_de_id=? ORDER BY cp.position", (cid,)).fetchall()
    ver = conn.execute(
        "SELECT version,pdf_path,quality_score,change_note FROM chuyen_de_versions "
        "WHERE chuyen_de_id=? ORDER BY version DESC", (cid,)).fetchall()
    return cd, members, ver


def active_rules(conn):
    return conn.execute(
        "SELECT id,category,rule_text FROM feedback_rules WHERE active=1 ORDER BY category,id").fetchall()
