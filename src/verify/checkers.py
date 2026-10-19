"""Deterministic math checkers (sympy + numeric + bounded brute force).

These are the real authority — NOT "several LLMs agree". Each returns
(result, detail) where result is True (passes), False (counterexample found),
or None (cannot decide -> needs_human).
"""
from __future__ import annotations
import itertools
import random
import sympy as sp


def symbolic_equivalence(expr_a: str, expr_b: str):
    try:
        diff = sp.simplify(sp.sympify(expr_a) - sp.sympify(expr_b))
        return (diff == 0), f"simplify(a-b) = {diff}"
    except Exception as e:  # parse error
        return None, f"parse/simplify error: {e}"


def numeric_equivalence(expr_a: str, expr_b: str, var_names: str,
                        trials: int = 40, lo: float = 0.2, hi: float = 6.0,
                        tol: float = 1e-6):
    try:
        syms = sp.symbols(var_names)
        syms = syms if isinstance(syms, tuple) else (syms,)
        a = sp.sympify(expr_a)
        b = sp.sympify(expr_b)
        fa = sp.lambdify(syms, a, "math")
        fb = sp.lambdify(syms, b, "math")
    except Exception as e:
        return None, f"parse error: {e}"
    tested = 0
    for _ in range(trials):
        vals = [random.uniform(lo, hi) for _ in syms]
        try:
            va, vb = fa(*vals), fb(*vals)
        except Exception:
            continue  # domain error (div by zero, sqrt<0) -> skip point
        if isinstance(va, complex) or isinstance(vb, complex):
            continue
        tested += 1
        if abs(va - vb) > tol * max(1.0, abs(va)):
            return False, f"counterexample at {dict(zip(map(str, syms), vals))}: {va} != {vb}"
    if tested < 5:
        return None, "insufficient valid sample points"
    return True, f"{tested} random points agree"


def check_roots(expr_str: str, roots, var: str = "x"):
    try:
        x = sp.symbols(var)
        e = sp.sympify(expr_str)
    except Exception as ex:
        return None, f"parse error: {ex}"
    bad = []
    for r in roots:
        try:
            if abs(complex(e.subs(x, r))) > 1e-9:
                bad.append(r)
        except Exception:
            bad.append(r)
    return (len(bad) == 0), ("all roots satisfy" if not bad else f"fail at {bad}")


def bounded_integer_solutions(expr_str: str, ranges: dict, target=0):
    """Exhaustive integer search in a box; returns the full solution set."""
    syms = {v: sp.symbols(v) for v in ranges}
    e = sp.sympify(expr_str)
    grids = [range(r[0], r[1] + 1) for r in ranges.values()]
    out = []
    for combo in itertools.product(*grids):
        subs = {syms[v]: val for v, val in zip(ranges.keys(), combo)}
        if e.subs(subs) == target:
            out.append(dict(zip(ranges.keys(), combo)))
    return out
