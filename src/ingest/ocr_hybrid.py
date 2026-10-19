"""
Hybrid local OCR: Pix2Text (formulas -> LaTeX) + EasyOCR-vi (Vietnamese prose),
merged by layout boxes. Validated on real khoi_9 scans.

Boundary (proven empirically): dense multi-inline-formula blocks have unreliable
Pix2Text inline coordinates -> flagged `needs_scalpel` for the Gemini fallback,
NOT stitched locally. See src/ingest/scalpel.py.

Models are CPU-only here (torch cuda=False); ~1-2 min/page.
"""
from __future__ import annotations
import statistics
import numpy as np
from PIL import Image

# --- runtime shim: rapidocr renamed resolve_model_key -> route_to_model_key ---
import rapidocr.utils.model_resolver as _mr
if not hasattr(_mr, "resolve_model_key"):
    _mr.resolve_model_key = _mr.route_to_model_key

from pix2text import Pix2Text  # noqa: E402
import easyocr  # noqa: E402

_P2T = None
_READER = None

MATH_TOKENS = ['\\frac', '\\sqrt', '\\perp', '\\cdot', '\\times', '\\le', '\\ge',
               '\\leq', '\\geq', '\\alpha', '\\beta', '\\gamma', '\\sum', '\\int',
               '\\angle', '\\pi', '\\pm', '\\cfrac', '\\begin', '^', '_', '=',
               '\\left', '\\right']

DROP_WORD_FRAC = 0.55       # drop EasyOCR word if >=55% of it sits over a formula box
CONTAIN_THR = 0.70          # drop element if >=70% contained in a >= sized element
SCALPEL_INLINE_COUNT = 4    # >= this many inline formulas in one block -> route to scalpel
SCALPEL_MIN_CONF = 0.30     # min element confidence below which -> route to scalpel


def get_p2t():
    global _P2T
    if _P2T is None:
        _P2T = Pix2Text.from_config()
    return _P2T


def get_reader():
    global _READER
    if _READER is None:
        _READER = easyocr.Reader(['vi'], gpu=False)
    return _READER


def is_real_formula(s: str) -> bool:
    return any(t in s for t in MATH_TOKENS)


def _clean_latex(t: str) -> str:
    return t.replace('$$', '').replace('\n', ' ').strip().strip('$').strip()


def _esc(s: str) -> str:
    for a, b in [('\\', r'\textbackslash{}'), ('&', r'\&'), ('%', r'\%'), ('#', r'\#'),
                 ('_', r'\_'), ('{', r'\{'), ('}', r'\}'), ('$', r'\$'), ('^', r'\^{}')]:
        s = s.replace(a, b)
    return s


def _bbox(pos):
    p = np.array(pos, dtype=float)
    return [float(p[:, 0].min()), float(p[:, 1].min()), float(p[:, 0].max()), float(p[:, 1].max())]


def _area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inter(a, b):
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    return max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)


def _dedup_elements(els):
    """Indices to drop: element >=CONTAIN_THR contained in another >= sized element."""
    drop = set()
    boxes = [e.box for e in els]
    for i in range(len(els)):
        if i in drop:
            continue
        for j in range(len(els)):
            if i == j or j in drop:
                continue
            ai = _area(boxes[i])
            if ai > 0 and _inter(boxes[i], boxes[j]) / ai >= CONTAIN_THR and _area(boxes[j]) >= ai:
                drop.add(i)
                break
    return drop


def _assemble_text_element(el, img):
    """Returns (latex_text, min_conf, inline_formula_count)."""
    x0, y0, x1, y1 = el.box
    crop = img.crop((x0, y0, x1, y1)).convert("RGB")
    fitems, fboxes = [], []
    meta = el.meta if isinstance(el.meta, list) else []
    for m in meta:
        if m.get('type') in ('embedding', 'isolated') and m.get('position') is not None:
            latex = _clean_latex(m.get('text', ''))
            if is_real_formula(latex):
                b = _bbox(m['position'])
                fitems.append((b[1], b[0], 'M', '$' + latex + '$'))
                fboxes.append(b)
    res = get_reader().readtext(np.array(crop), detail=1, paragraph=False)
    items = list(fitems)
    heights, confs = [], []
    for bb, text, conf in res:
        xs = [p[0] for p in bb]
        ys = [p[1] for p in bb]
        wbox = [min(xs), min(ys), max(xs), max(ys)]
        wa = _area(wbox)
        if any(wa > 0 and _inter(wbox, fb) / wa >= DROP_WORD_FRAC for fb in fboxes):
            continue
        heights.append(max(ys) - min(ys))
        confs.append(conf)
        items.append((min(ys), min(xs), 'T', _esc(text)))
    if not items:
        return "", 1.0, len(fitems)
    thr = 0.6 * statistics.median(heights) if heights else 30
    items.sort(key=lambda z: (z[0], z[1]))
    lines, cur, ref = [], [], None
    for it in items:
        if ref is None or (it[0] - ref) < thr:
            cur.append(it)
            ref = it[0] if ref is None else min(ref, it[0])
        else:
            lines.append(cur)
            cur = [it]
            ref = it[0]
    if cur:
        lines.append(cur)
    out = []
    for ln in lines:
        ln.sort(key=lambda z: z[1])
        out.append(' '.join(x[3] for x in ln))
    return ' '.join(out), (min(confs) if confs else 1.0), len(fitems)


def page_to_elements(image_path: str):
    """Run hybrid OCR on a rendered page image.

    Returns list of dicts: {idx, el_type, bbox, content, source_engine,
    confidence, needs_scalpel}. Dense-inline/low-conf text elements are flagged
    needs_scalpel=1 (their `content` is still the best-effort local merge).
    """
    img = Image.open(image_path)
    page = get_p2t().recognize(image_path, file_type="page", return_text=False)
    drop = _dedup_elements(page.elements)
    out = []
    for idx, el in enumerate(page.elements):
        if idx in drop:
            continue
        t = str(el.type)
        if 'FORMULA' in t:
            latex = _clean_latex(el.meta['text'] if isinstance(el.meta, dict) else el.text)
            out.append(dict(idx=idx, el_type='formula', bbox=list(el.box),
                            content=latex, source_engine='pix2text',
                            confidence=float(getattr(el, 'score', 0.0) or 0.0),
                            needs_scalpel=0))
        else:
            txt, min_conf, n_inline = _assemble_text_element(el, img)
            if not txt.strip():
                continue
            needs = 1 if (n_inline >= SCALPEL_INLINE_COUNT or min_conf < SCALPEL_MIN_CONF) else 0
            out.append(dict(idx=idx, el_type=('title' if 'TITLE' in t else 'text'),
                            bbox=list(el.box), content=txt, source_engine='pix2text+easyocr',
                            confidence=float(min_conf), needs_scalpel=needs))
    return out


def elements_to_latex(elements) -> str:
    body = []
    for e in elements:
        if e['el_type'] == 'formula':
            body.append("\\[\n" + e['content'] + "\n\\]")
        else:
            body.append(e['content'] + "\n")
    return "\n\n".join(body)
