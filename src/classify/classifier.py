"""Rule/keyword classifier for tiers 1-2 (local, free). Multi-label.

Confidence scales with keyword-hit count. Tiers 3-4 (phương pháp, kỹ thuật tư duy)
and confident tier-5 difficulty are a later LLM step; tier-1/2 keyword matching is
reliable and gives the dashboard human-confirmable labels.
"""
from __future__ import annotations
from src.extract.textnorm import normalize
from src.classify.taxonomy_seed import TAXONOMY, seed_taxonomy


def _conf(hits: int) -> float:
    return min(1.0, 0.40 + 0.15 * hits)


def classify_text(norm: str):
    """Return list of (tier, name, confidence)."""
    out = []
    for t1, spec in TAXONOMY.items():
        hits = sum(1 for kw in spec["kw"] if kw in norm)
        if hits == 0:
            continue
        out.append((1, t1, _conf(hits)))
        for t2, kws in spec["sub"].items():
            h2 = sum(1 for kw in kws if kw in norm)
            if h2 > 0:
                out.append((2, t2, _conf(h2)))
    return out


def classify_all(conn) -> dict:
    name2id = seed_taxonomy(conn)
    rows = conn.execute(
        "SELECT id,statement_latex,hypotheses,conclusion FROM problems").fetchall()
    n_prob, n_labels, n_unclassified = 0, 0, 0
    for r in rows:
        text = " ".join(x for x in (r["statement_latex"], r["hypotheses"],
                                    r["conclusion"]) if x)
        norm = normalize(text)
        labels = classify_text(norm)
        # idempotent: clear previous rule-based labels for this problem
        conn.execute("DELETE FROM classifications WHERE problem_id=? AND assigned_by='rule'",
                     (r["id"],))
        if not labels:
            n_unclassified += 1
        for tier, name, conf in labels:
            conn.execute(
                """INSERT INTO classifications
                   (problem_id,tier,taxonomy_id,label,confidence,assigned_by,confirmed)
                   VALUES (?,?,?,?,?, 'rule', 0)""",
                (r["id"], tier, name2id.get(name), name, conf))
            n_labels += 1
        conn.execute("UPDATE problems SET status='classified' WHERE id=?", (r["id"],))
        n_prob += 1
    conn.commit()
    return {"problems": n_prob, "labels": n_labels, "unclassified": n_unclassified}
