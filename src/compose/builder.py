"""Compose a chuyên đề: select -> assemble LaTeX -> compile PDF -> write DB."""
from __future__ import annotations
import os
import subprocess
from src.compose.selector import select_problems
from src.compose.latex_template import build_tex


def compose(conn, title, grade, label, difficulty, limit, xelatex, out_dir, provider=None):
    rows = select_problems(conn, grade=grade, label=label, difficulty=difficulty, limit=limit)
    if not rows:
        return None, "no problems matched the selection"

    intro = None
    if provider is not None:
        try:
            from src.compose.prose import generate_intro
            intro = generate_intro(provider, conn, title, grade, label)
        except Exception as e:
            print(f"[compose] LLM intro failed, using template: {e}")

    cur = conn.execute(
        "INSERT INTO chuyen_de (title,grade,status,current_version) VALUES (?,?, 'draft', 1)",
        (title, grade))
    cid = cur.lastrowid

    items = []
    for i, r in enumerate(rows):
        role = "example" if i == 0 else "exercise"   # first as a worked-example slot
        conn.execute(
            "INSERT INTO chuyen_de_problems (chuyen_de_id,problem_id,position,role) "
            "VALUES (?,?,?,?)", (cid, r["id"], i + 1, role))
        items.append((role, r["statement_latex"]))

    tex = build_tex(title, grade, label, items, intro=intro)
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, f"chuyende_{cid}_v1")
    texp = base + ".tex"
    with open(texp, "w", encoding="utf-8") as fh:
        fh.write(tex)

    rc, pdfp = None, None
    try:
        res = subprocess.run(
            [xelatex, "-interaction=nonstopmode", "-halt-on-error",
             "-output-directory", out_dir, texp],
            capture_output=True, text=True, cwd=out_dir, timeout=240)
        rc = res.returncode
        if os.path.exists(base + ".pdf"):
            pdfp = base + ".pdf"
    except Exception as e:
        rc = f"compile error: {e}"

    conn.execute(
        "INSERT INTO chuyen_de_versions (chuyen_de_id,version,content_latex,pdf_path,change_note) "
        "VALUES (?,?,?,?,?)", (cid, 1, tex, pdfp, "initial template compose"))
    conn.commit()
    return dict(chuyen_de_id=cid, problems=len(rows), tex=texp, pdf=pdfp, compile_rc=rc), None
