"""Select problems for a chuyên đề by label (taxonomy) / grade / difficulty."""
from __future__ import annotations


def select_problems(conn, grade=None, label=None, difficulty=None, limit=None):
    q = "SELECT DISTINCT p.id, p.statement_latex, p.grade_level FROM problems p"
    where, params = [], []
    if label:
        q += " JOIN classifications c ON c.problem_id = p.id"
        where.append("c.label = ?")
        params.append(label)
    if difficulty:
        q += " JOIN difficulty d ON d.problem_id = p.id"
        where.append("d.score = ?")
        params.append(difficulty)
    if grade:
        where.append("p.grade_level = ?")
        params.append(grade)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY p.id"
    rows = conn.execute(q, params).fetchall()
    return rows[:limit] if limit else rows
