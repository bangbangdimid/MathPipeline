"""LaTeX template for a chuyên đề (template-first; LLM prose comes later).

v0 assembles selected problems into numbered Ví dụ / Bài tập with a Mục tiêu
placeholder. The composer is intentionally NOT the source of mathematical truth;
it lays out verified/extracted content in your style.
"""
from __future__ import annotations
import re

_LEAD = re.compile(r'^\s*(Bài|Câu|BÀI|CÂU)\s+[0-9IVXLivxl]+\s*[:.]?\s*(\([^)]*\))?\s*', re.I)


def strip_marker(s: str) -> str:
    return _LEAD.sub('', s, count=1).strip()


def _esc_title(s: str) -> str:
    for a, b in [('&', r'\&'), ('%', r'\%'), ('#', r'\#'), ('_', r'\_')]:
        s = s.replace(a, b)
    return s


_HEADER = r"""\documentclass[12pt]{article}
\usepackage{fontspec}
\setmainfont{Times New Roman}
\usepackage{amsmath,amssymb,amsthm}
\usepackage[a4paper,margin=2.2cm]{geometry}
\theoremstyle{definition}
\newtheorem{vidu}{Ví dụ}
\newtheorem{baitap}{Bài tập}
\setlength{\parindent}{0pt}\setlength{\parskip}{6pt}
\title{%s}
\author{}\date{}
\begin{document}
\maketitle
"""


def build_tex(title: str, grade: str | None, label: str | None, items,
              intro: str | None = None) -> str:
    body = [_HEADER % _esc_title(title)]
    body.append(r"\section*{Mục tiêu}")
    if intro:
        body.append(_esc_title(intro))
    else:
        body.append(
            f"Chuyên đề tổng hợp các bài toán {_esc_title(label or '')} dành cho "
            f"{_esc_title(grade or 'THCS')}. "
            r"\textit{(Phần dẫn nhập lý thuyết, phân tích hướng giải và sai lầm thường gặp "
            r"sẽ được bổ sung ở bước sinh nội dung bằng LLM.)}")
    examples = [s for role, s in items if role == "example"]
    exercises = [s for role, s in items if role != "example"]
    if examples:
        body.append(r"\section*{Ví dụ mẫu}")
        for s in examples:
            body.append(r"\begin{vidu}" + "\n" + strip_marker(s) + "\n" + r"\end{vidu}")
    body.append(r"\section*{Hệ thống bài tập}")
    for s in exercises:
        body.append(r"\begin{baitap}" + "\n" + strip_marker(s) + "\n" + r"\end{baitap}")
    body.append(r"\end{document}")
    return "\n\n".join(body)
