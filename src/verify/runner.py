"""Independent verification runner.

Reads a solution's machine-readable check-spec (JSON in solutions.method_tags,
key `verify_kind` + `verify_spec`), runs the matching deterministic checker, and
writes a row to `verification` with an explicit status. Runs separate from any
compose step — the composer never grades itself.

Status mapping:
  tool_verified      - symbolic proof / exact substitution passed
  numerically_tested - numeric agreement / bounded exhaustive search
  contradiction_found- a counterexample was found
  needs_human        - cannot decide (geometry proof, parse failure, no claim)
"""
from __future__ import annotations
import json
from src.verify import checkers as ck


def _simplify(spec):
    ok, det = ck.symbolic_equivalence(spec["a"], spec["b"])
    if ok is True:
        return "tool_verified", "sympy", "symbolic: " + det
    if ok is False:
        n, nd = ck.numeric_equivalence(spec["a"], spec["b"], spec["vars"])
        if n is False:
            return "contradiction_found", "sympy+numeric", nd
        return "needs_human", "sympy", "symbolic!=0, numeric inconclusive: " + det
    n, nd = ck.numeric_equivalence(spec["a"], spec["b"], spec["vars"])
    if n is True:
        return "numerically_tested", "numeric", nd
    if n is False:
        return "contradiction_found", "numeric", nd
    return "needs_human", "none", det


def _roots(spec):
    ok, det = ck.check_roots(spec["expr"], spec["roots"], spec.get("var", "x"))
    if ok is True:
        return "tool_verified", "sympy", "roots substituted: " + det
    if ok is False:
        return "contradiction_found", "sympy", det
    return "needs_human", "none", det


def _diophantine(spec):
    sols = ck.bounded_integer_solutions(spec["expr"], spec["ranges"], spec.get("target", 0))
    claim = spec.get("claim")
    if claim is None:
        return "needs_human", "bruteforce", f"found {len(sols)} sols in box (no claim to compare)"
    norm = lambda L: sorted(tuple(sorted(d.items())) for d in L)
    status = "numerically_tested" if norm(sols) == norm(claim) else "contradiction_found"
    return status, "bruteforce", f"box_sols={sols} claim={claim}"


DISPATCH = {"simplify_equiv": _simplify, "roots": _roots, "diophantine": _diophantine}


def verify_spec(kind, spec):
    if kind == "geometry_proof":
        return "needs_human", "none", "synthetic geometry proof: no automated certifier at THCS scope"
    fn = DISPATCH.get(kind)
    if not fn:
        return "needs_human", "none", f"unknown verify_kind: {kind}"
    try:
        return fn(spec)
    except Exception as e:
        return "needs_human", "none", f"checker error: {e}"


def verify_all(conn) -> int:
    rows = conn.execute(
        "SELECT id,method_tags FROM solutions WHERE method_tags IS NOT NULL").fetchall()
    n = 0
    for r in rows:
        try:
            spec = json.loads(r["method_tags"])
        except Exception:
            continue
        if "verify_kind" not in spec:
            continue
        status, tool, ev = verify_spec(spec["verify_kind"], spec.get("verify_spec", {}))
        conn.execute(
            "INSERT INTO verification (solution_id,status,tool_used,evidence,checked_by) "
            "VALUES (?,?,?,?, 'auto')", (r["id"], status, tool, ev))
        n += 1
    conn.commit()
    return n


# ---- demonstration on corpus-representative claims (self-cleaning) ----
_DEMO = [
    ("Rút gọn ĐÚNG: (x^2-1)/(x-1) = x+1", "simplify_equiv",
     {"a": "(x**2-1)/(x-1)", "b": "x+1", "vars": "x"}),
    ("Rút gọn SAI: (x^2-1)/(x-1) = x-1", "simplify_equiv",
     {"a": "(x**2-1)/(x-1)", "b": "x-1", "vars": "x"}),
    ("Nghiệm PT ĐÚNG: x^2-5x+6, {2,3}", "roots",
     {"expr": "x**2-5*x+6", "roots": [2, 3], "var": "x"}),
    ("Nghiệm PT SAI: x^2-5x+6, {2,4}", "roots",
     {"expr": "x**2-5*x+6", "roots": [2, 4], "var": "x"}),
    ("Nghiệm nguyên (HSG): 5x^2+2xy+y^2-4x-40=0", "diophantine",
     {"expr": "5*x**2+2*x*y+y**2-4*x-40", "ranges": {"x": [-10, 10], "y": [-10, 10]},
      "claim": [{"x": -4, "y": 8}, {"x": 2, "y": -4}]}),
    ("Hình học: CM hai tam giác đồng dạng", "geometry_proof", {}),
]


def verify_demo(conn):
    created = []
    for name, kind, spec in _DEMO:
        cur = conn.execute(
            "INSERT INTO problems (statement_latex,extracted_by,status) VALUES (?, 'verify_demo','extracted')",
            (name,))
        pid = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO solutions (problem_id,solution_latex,origin,method_tags) "
            "VALUES (?,?, 'verify_demo', ?)",
            (pid, name, json.dumps({"verify_kind": kind, "verify_spec": spec})))
        created.append((name, cur.lastrowid))
    conn.commit()
    verify_all(conn)
    out = []
    for name, sid in created:
        row = conn.execute(
            "SELECT status,tool_used,evidence FROM verification WHERE solution_id=?",
            (sid,)).fetchone()
        out.append((name, row["status"], row["tool_used"], row["evidence"]))
    # cleanup synthetic demo rows
    conn.execute("DELETE FROM verification WHERE solution_id IN "
                 "(SELECT id FROM solutions WHERE origin='verify_demo')")
    conn.execute("DELETE FROM solutions WHERE origin='verify_demo'")
    conn.execute("DELETE FROM problems WHERE extracted_by='verify_demo'")
    conn.commit()
    return out
