"""Segment a page's reconstructed LaTeX into problem records.

Rule-based (local, free): split on Vietnamese problem markers (Bài/Câu N),
then best-effort split statement vs solution and a hypotheses/conclusion heuristic.
Deeper hypotheses/conclusion extraction is a later LLM step; this is honest v0.
"""
from __future__ import annotations
import re
from src.extract.textnorm import canonical_hash

PROBLEM_RE = re.compile(r'(?im)^\s*(Bài|Câu|BÀI|CÂU)\s+([0-9IVXLivxl]+)\b')
SOLUTION_MARK = re.compile(r'(?i)(Lời giải|Hướng dẫn giải|Hướng dẫn|Đáp án|Lời giải chi tiết|Giải\s*:)')
# Trailing exam boilerplate to cut off the end of a statement.
BOILERPLATE = re.compile(
    r'(?i)(-{2,}\s*)?\bH[ẾÉE]T\b'
    r'|Gi[áa]m th[ịi] coi'
    r'|C[áa]n b[ộo] coi thi'
    r'|coi thi kh[ôo]ng gi'
    r'|H[ọo]c sinh kh[ôo]ng (đ|d)[ưuợ]+c d[ùu]ng')


def strip_boilerplate(s: str) -> str:
    m = BOILERPLATE.search(s)
    if m and m.start() > 20:   # don't nuke a whole short statement
        return s[:m.start()].rstrip()
    return s
CONCL_VERBS = re.compile(
    r'(?i)\b(Chứng minh|Chứng tỏ|Tính|Tìm|Giải|Xác định|So sánh|Rút gọn|Vẽ|Lập|Cho biết|Hỏi)\b')


def split_solution(text: str):
    m = SOLUTION_MARK.search(text)
    if m:
        return text[:m.start()].strip(), text[m.start():].strip()
    return text.strip(), None


def split_hyp_concl(statement: str):
    m = CONCL_VERBS.search(statement)
    if m and m.start() > 0:
        return statement[:m.start()].strip() or None, statement[m.start():].strip()
    return None, (statement.strip() or None)


def split_problems(page_latex: str):
    matches = list(PROBLEM_RE.finditer(page_latex))
    chunks = []
    if not matches:
        chunks.append((None, page_latex.strip()))
    else:
        if matches[0].start() > 0:
            head = page_latex[:matches[0].start()].strip()
            # ignore header/boilerplate before first problem marker
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(page_latex)
            num = f"{m.group(1).title()} {m.group(2)}"
            chunks.append((num, page_latex[start:end].strip()))
    problems = []
    for num, text in chunks:
        if len(text) < 8:
            continue
        statement, solution = split_solution(text)
        statement = strip_boilerplate(statement)
        hyp, concl = split_hyp_concl(statement)
        problems.append(dict(number=num, statement_latex=statement,
                             hypotheses=hyp, conclusion=concl, solution_latex=solution))
    return problems


def segment_document(conn, doc_id: int) -> int:
    rows = conn.execute(
        "SELECT id,page_no,latex FROM pages WHERE document_id=? AND latex IS NOT NULL "
        "ORDER BY page_no", (doc_id,)).fetchall()
    created = 0
    for r in rows:
        for p in split_problems(r["latex"]):
            ch = canonical_hash(p["statement_latex"])
            cur = conn.execute(
                """INSERT INTO problems
                   (statement_latex,hypotheses,conclusion,grade_level,canonical_hash,
                    extracted_by,status)
                   VALUES (?,?,?,(SELECT grade FROM documents WHERE id=?),?,?,'extracted')""",
                (p["statement_latex"], p["hypotheses"], p["conclusion"], doc_id, ch, "rule_based"))
            pid = cur.lastrowid
            conn.execute(
                "INSERT INTO provenance (problem_id,document_id,page_no) VALUES (?,?,?)",
                (pid, doc_id, r["page_no"]))
            if p["solution_latex"]:
                conn.execute(
                    "INSERT INTO solutions (problem_id,solution_latex,origin) VALUES (?,?, 'original')",
                    (pid, p["solution_latex"]))
            created += 1
    conn.commit()
    return created
