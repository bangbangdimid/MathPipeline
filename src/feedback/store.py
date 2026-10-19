"""Feedback loop: record edits -> distill into reusable rules -> inject into
future compose prompts. No fine-tuning (respects the 0đ constraint); the learning
is RAG-over-your-own-corrections: active rules are prepended to the LLM prompt.
"""
from __future__ import annotations
from src.extract.textnorm import normalize

CORRECTION_ACTIONS = {"reject", "edit", "rewrite"}

_CATEGORY_KW = {
    "style": ["trinh bay", "ky hieu", "format", "bo cuc", "em dash", "tran",
              "font", "canh le", "danh so", "nhan", "latex"],
    "math": ["sai", "chung minh", "dieu kien", "phan vi du", "dap so", "nham",
             "thieu buoc", "lam tron", "nghiem"],
    "pedagogy": ["kho", "de hon", "lop", "buoc", "giai thich", "vi du",
                 "dong co", "muc tieu", "loc nhan", "lanh vuc"],
}


def guess_category(text: str) -> str:
    n = normalize(text or "")
    best, best_hits = "style", 0
    for cat, kws in _CATEGORY_KW.items():
        hits = sum(1 for k in kws if k in n)
        if hits > best_hits:
            best, best_hits = cat, hits
    return best


def record_feedback(conn, target_type, target_id, action, reason=None,
                    before=None, after=None, editor="human"):
    cur = conn.execute(
        """INSERT INTO feedback
           (target_type,target_id,editor,action,before_text,after_text,reason)
           VALUES (?,?,?,?,?,?,?)""",
        (target_type, target_id, editor, action, before, after, reason))
    conn.commit()
    return cur.lastrowid


def add_rule(conn, rule_text, category=None, derived_from=None):
    category = category or guess_category(rule_text)
    cur = conn.execute(
        "INSERT INTO feedback_rules (rule_text,category,derived_from,active) VALUES (?,?,?,1)",
        (rule_text, category, derived_from))
    conn.commit()
    return cur.lastrowid, category


def active_rules(conn, category=None):
    if category:
        return conn.execute(
            "SELECT id,rule_text,category FROM feedback_rules WHERE active=1 AND category=? "
            "ORDER BY id", (category,)).fetchall()
    return conn.execute(
        "SELECT id,rule_text,category FROM feedback_rules WHERE active=1 "
        "ORDER BY category,id").fetchall()


def deactivate_rule(conn, rule_id):
    conn.execute("UPDATE feedback_rules SET active=0 WHERE id=?", (rule_id,))
    conn.commit()


def rules_for_prompt(conn, category=None) -> str:
    """Prompt-ready block of active rules — injected into compose/verify prompts."""
    rows = active_rules(conn, category)
    if not rows:
        return ""
    lines = ["# Quy tắc rút ra từ phản hồi trước (bắt buộc tuân thủ):"]
    for r in rows:
        lines.append(f"- [{r['category']}] {r['rule_text']}")
    return "\n".join(lines)
