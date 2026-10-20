"""MathPipeline review dashboard — FastAPI + HTMX + MathJax (local, light).

Run:  python src/pipeline.py dashboard   (or: uvicorn src.dashboard.app:app)
Tracks progress, lets you review/edit/reject problems and chuyên đề, and records
feedback (which distils into feedback_rules that future compose prompts obey).
"""
from __future__ import annotations
import html
import os
import threading
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, FileResponse

from src.config import load_settings, load_providers, abspath
from src import db as dbmod
from src.dashboard import queries as Q
from src.feedback import store as FB
from src.extract.textnorm import canonical_hash

app = FastAPI(title="MathPipeline Dashboard")
_DB = abspath(load_settings()["paths"]["db"])


def conn():
    c = dbmod.get_conn(_DB)
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=60000")
    except Exception:
        pass
    return c


# Names of background jobs currently running (shown live in the UI).
RUNNING: dict = {}
_RLOCK = threading.Lock()


def _launch(name, fn):
    with _RLOCK:
        if name in RUNNING:
            return f"<div class='ok'>“{esc(name)}” đang chạy rồi — xem tiến độ bên dưới.</div>"
        RUNNING[name] = True

    def wrap():
        try:
            fn()
        except Exception as e:
            print(f"[dashboard task {name}] error: {e}", flush=True)
        finally:
            RUNNING.pop(name, None)

    threading.Thread(target=wrap, daemon=True).start()
    return f"<div class='ok'>Đã khởi động: <b>{esc(name)}</b>. Tiến độ cập nhật bên dưới.</div>"


def esc(x):
    return html.escape(str(x if x is not None else ""))


_HEAD = """<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MathPipeline</title>
<script src="https://cdn.jsdelivr.net/npm/htmx.org@1.9.12/dist/htmx.min.js"></script>
<script>window.MathJax={tex:{inlineMath:[['$','$']],displayMath:[['\\\\[','\\\\]']]},
 options:{skipHtmlTags:['script','noscript','style','textarea']}};</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/tex-mml-chtml.js" async></script>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1c2230;--muted:#6b7280;--line:#e5e7eb;
 --accent:#2f6f4e;--accent2:#b45309;--danger:#b91c1c;--radius:12px}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
 font:15px/1.55 'Segoe UI',system-ui,sans-serif}
header{background:var(--card);border-bottom:1px solid var(--line);padding:14px 22px;
 display:flex;align-items:center;gap:22px;position:sticky;top:0;z-index:5}
header b{font-size:18px;letter-spacing:.3px}
nav a{color:var(--muted);text-decoration:none;margin-right:16px;font-weight:600}
nav a:hover{color:var(--accent)}
main{max-width:1060px;margin:22px auto;padding:0 18px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
 padding:16px 18px}
.card .n{font-size:30px;font-weight:700;color:var(--accent)}
.card .l{color:var(--muted);font-size:13px;margin-top:2px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);
 border-radius:var(--radius);overflow:hidden}
th,td{padding:10px 12px;text-align:left;border-bottom:1px solid var(--line);font-size:14px}
th{background:#eef1f4;color:var(--muted);font-weight:600}
tr:last-child td{border-bottom:0}tr:hover td{background:#fafbfc}
a.lk{color:var(--accent);text-decoration:none;font-weight:600}
.tag{display:inline-block;background:#eef5f0;color:var(--accent);border-radius:20px;
 padding:2px 10px;font-size:12px;margin:2px 3px 0 0}
.tag.t2{background:#fdf3e7;color:var(--accent2)}
.statement{background:#fbfbfc;border:1px solid var(--line);border-radius:10px;padding:14px 16px;
 margin:12px 0;white-space:pre-wrap}
.pill{font-size:12px;padding:2px 10px;border-radius:20px;background:#eef1f4;color:var(--muted)}
.pill.flagged{background:#fde7e7;color:var(--danger)}
.pill.published{background:#e7f3ec;color:var(--accent)}
form.fb{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
 padding:16px;margin-top:14px}
label{display:block;font-size:13px;color:var(--muted);margin:10px 0 4px}
select,textarea,input{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:8px;
 font:inherit}textarea{min-height:70px;resize:vertical}
button{background:var(--accent);color:#fff;border:0;border-radius:8px;padding:9px 18px;
 font-weight:600;cursor:pointer;margin-top:12px}button:hover{filter:brightness(1.07)}
.ok{background:#e7f3ec;color:var(--accent);border:1px solid #cfe7d8;border-radius:8px;
 padding:10px 12px;margin-top:10px}
.muted{color:var(--muted)}h2{margin:6px 0 16px}
.barwrap{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}
</style></head><body>
<header><b>▚ MathPipeline</b>
<nav><a href="/">Tổng quan</a><a href="/problems">Bài toán</a>
<a href="/chuyende">Chuyên đề</a><a href="/rules">Quy tắc</a></nav></header>
<main hx-on::after-swap="if(window.MathJax&&MathJax.typesetPromise)MathJax.typesetPromise()">
"""
_FOOT = "</main></body></html>"


def page(body):
    return HTMLResponse(_HEAD + body + _FOOT)


_CORPUS_DEFAULT = r"D:\Downloads\tai_lieu_toan_thcs_full"


def _jobs_status(c):
    return {r["status"]: r["n"] for r in c.execute(
        "SELECT status, COUNT(*) n FROM jobs WHERE job_type='ingest_doc' GROUP BY status")}


def _progress_html():
    c = conn(); s = Q.stats(c); js = _jobs_status(c); c.close()
    cards = [("Tài liệu", s["documents"]), ("Trang", s["pages"]),
             ("Bài toán", s["problems"]), ("Đã phân loại", s["classified"]),
             ("Cặp trùng", s["duplicates"]), ("Chuyên đề", s["chuyende"]),
             ("Cờ needs_scalpel", s["needs_scalpel"]), ("Bài bị gắn cờ", s["flagged"]),
             ("Quy tắc active", s["rules"])]
    grid = "".join(f"<div class='card'><div class='n'>{v}</div><div class='l'>{esc(l)}</div></div>"
                   for l, v in cards)
    jb = " ".join(f"<span class='pill'>{esc(k)}: {v}</span>" for k, v in js.items()) \
        or "<span class='muted'>chưa có job</span>"
    pbs = " ".join(f"<span class='pill'>{esc(k)}: {v}</span>"
                   for k, v in s["pages_by_status"].items()) or "<span class='muted'>—</span>"
    vbs = " ".join(f"<span class='pill'>{esc(k)}: {v}</span>"
                   for k, v in s["verify_by_status"].items()) or "<span class='muted'>chưa có</span>"
    remaining = js.get("queued", 0) + js.get("retry", 0)
    total = sum(js.values()) or 0
    pct = int(100 * js.get("done", 0) / total) if total else 0
    running = ", ".join(RUNNING.keys())
    run_line = (f"<p class='muted'>⏳ Đang chạy: <b>{esc(running)}</b></p>" if running
                else "<p class='muted'>Không có tác vụ nào đang chạy.</p>")
    remain_card = (f"<div class='card' style='border-color:var(--accent)'>"
                   f"<div class='n'>{remaining}</div>"
                   f"<div class='l'>file CÒN LẠI trong hàng đợi · đã xong {pct}%</div></div>")
    return (f"<div class='grid'>{remain_card}{grid}</div>{run_line}"
            f"<p class='muted'>Jobs ingest:</p><div class='barwrap'>{jb}</div>"
            f"<p class='muted'>Trạng thái OCR trang:</p><div class='barwrap'>{pbs}</div>"
            f"<p class='muted'>Trạng thái kiểm chứng:</p><div class='barwrap'>{vbs}</div>")


@app.get("/", response_class=HTMLResponse)
def home():
    control = f"""<h2>Điều khiển</h2>
<form class="fb" hx-post="/run/enqueue" hx-target="#runmsg" style="margin-bottom:10px">
 <label>Thư mục corpus để nạp hàng đợi</label>
 <input name="dir" value="{esc(_CORPUS_DEFAULT)}">
 <label>Giới hạn số file (để trống = tất cả)</label>
 <input name="limit" placeholder="vd: 50">
 <button type="submit">Enqueue (nạp hàng đợi)</button></form>
<div class="barwrap">
 <button hx-post="/run/ingest" hx-target="#runmsg">▶ Chạy ingest (tự khởi động lại)</button>
 <button hx-post="/run/requeue" hx-target="#runmsg">Chạy lại file lỗi</button>
 <button hx-post="/run/build" hx-target="#runmsg">Dựng ngân hàng bài</button>
 <button hx-post="/run/scalpel" hx-target="#runmsg">Scalpel (Gemini)</button></div>
<div id="runmsg" style="margin-top:10px"></div>"""
    prog = ("<h2 style='margin-top:26px'>Tiến độ (tự cập nhật mỗi 3s)</h2>"
            "<div hx-get='/progress' hx-trigger='load, every 3s'>" + _progress_html() + "</div>")
    return page(control + prog)


@app.get("/progress", response_class=HTMLResponse)
def progress():
    return HTMLResponse(_progress_html())


@app.post("/run/enqueue", response_class=HTMLResponse)
def run_enqueue(dir: str = Form(...), limit: str = Form("")):
    from src.orchestrator import queue as OQ
    lim = int(limit) if limit.strip().isdigit() else None
    if not os.path.isdir(dir):
        return HTMLResponse(f"<div class='ok'>Không thấy thư mục: {esc(dir)}</div>")
    c = conn()
    added, total = OQ.enqueue_dir(c, dir, None, lim, 0)
    c.close()
    return HTMLResponse(f"<div class='ok'>Enqueue: thêm <b>{added}</b> job mới (tổng {total} pdf).</div>")


def _supervise_job():
    from src.orchestrator.supervisor import supervise
    s = load_settings()
    workers = int((s.get("ocr", {}) or {}).get("workers", 2) or 2)
    supervise(abspath(s["paths"]["db"]), abspath(s["paths"]["renders"]),
              workers=workers, restart_wait=10)


@app.post("/run/ingest", response_class=HTMLResponse)
def run_ingest():
    return HTMLResponse(_launch("ingest (OCR, tự khởi động lại)", _supervise_job))


@app.post("/run/requeue", response_class=HTMLResponse)
def run_requeue():
    from src.orchestrator import queue as OQ
    c = conn()
    n = OQ.requeue_failed(c)
    c.close()
    launched = _launch("ingest (OCR, tự khởi động lại)", _supervise_job)
    return HTMLResponse(f"<div class='ok'>Đã đưa lại <b>{n}</b> file lỗi vào hàng đợi. {launched}</div>")


@app.post("/run/build", response_class=HTMLResponse)
def run_build():
    def job():
        from src.ingest.repropagate import repropagate
        c = conn(); repropagate(c); c.close()
    return HTMLResponse(_launch("dựng ngân hàng bài", job))


@app.post("/run/scalpel", response_class=HTMLResponse)
def run_scalpel():
    def job():
        from src.providers.base import get_active_provider
        p = get_active_provider(load_providers())
        if p is None:
            print("[dashboard] scalpel: no Gemini key", flush=True)
            return
        from src.ingest.scalpel import rescan_scalpel
        c = conn(); rescan_scalpel(c, p); c.close()
    return HTMLResponse(_launch("scalpel (Gemini)", job))


@app.get("/problems", response_class=HTMLResponse)
def problems(grade: str = "", label: str = ""):
    c = conn()
    labels = Q.tier1_labels(c)
    rows = Q.list_problems(c, grade or None, label or None)
    c.close()
    opts = "<option value=''>— tất cả lĩnh vực —</option>" + "".join(
        f"<option value='{esc(l)}'{' selected' if l==label else ''}>{esc(l)}</option>" for l in labels)
    filt = (f"<form method='get' style='margin-bottom:14px'>"
            f"<select name='label' onchange='this.form.submit()'>{opts}</select></form>")
    trs = "".join(
        f"<tr><td>{r['id']}</td><td>{esc(r['grade_level'])}</td>"
        f"<td><span class='pill {esc(r['status'])}'>{esc(r['status'])}</span></td>"
        f"<td class='muted'>{esc(r['s'])}…</td>"
        f"<td><a class='lk' href='/problems/{r['id']}'>mở →</a></td></tr>" for r in rows)
    return page(f"<h2>Bài toán ({len(rows)})</h2>{filt}"
                f"<table><tr><th>ID</th><th>Lớp</th><th>Trạng thái</th><th>Trích</th><th></th></tr>"
                f"{trs}</table>")


def _problem_detail_html(c, pid):
    p, labels, prov = Q.get_problem(c, pid)
    if not p:
        return "<p>Không tìm thấy bài toán.</p>"
    tags = "".join(f"<span class='tag{' t2' if l['tier']==2 else ''}'>T{l['tier']}:{esc(l['label'])} "
                   f"({l['confidence']:.2f})</span>" for l in labels) or "<span class='muted'>chưa phân loại</span>"
    src = "".join(f"<div class='muted'>{esc(os.path.basename(r['pdf_path']))} · trang {r['page_no']}</div>"
                  for r in prov) or "<span class='muted'>—</span>"
    stmt = esc(p["statement_latex"])
    form = f"""<form class="fb" hx-post="/problems/{pid}/feedback" hx-target="#fbres{pid}">
 <label>Hành động</label>
 <select name="action">
  <option value="approve">approve — duyệt</option>
  <option value="edit">edit — sửa nội dung</option>
  <option value="reject">reject — từ chối (gắn cờ)</option>
  <option value="rewrite">rewrite — yêu cầu viết lại</option></select>
 <label>Lý do (sẽ chưng cất thành quy tắc nếu có)</label>
 <textarea name="reason" placeholder="vd: bỏ phần HẾT dính vào đề…"></textarea>
 <label>Nội dung sửa (chỉ dùng khi chọn edit)</label>
 <textarea name="edited">{stmt}</textarea>
 <button type="submit">Ghi feedback</button>
 <div id="fbres{pid}"></div></form>"""
    return (f"<h2>Bài toán #{pid} <span class='pill {esc(p['status'])}'>{esc(p['status'])}</span></h2>"
            f"<div class='barwrap'>{tags}</div>"
            f"<div class='statement'>{stmt}</div>"
            f"<p class='muted'>Nguồn:</p>{src}{form}"
            f"<p style='margin-top:16px'><a class='lk' href='/problems'>← danh sách</a></p>")


@app.get("/problems/{pid}", response_class=HTMLResponse)
def problem_detail(pid: int):
    c = conn(); body = _problem_detail_html(c, pid); c.close()
    return page(body)


@app.post("/problems/{pid}/feedback", response_class=HTMLResponse)
def problem_feedback(pid: int, action: str = Form(...), reason: str = Form(""),
                     edited: str = Form("")):
    c = conn()
    row = c.execute("SELECT statement_latex FROM problems WHERE id=?", (pid,)).fetchone()
    before = row["statement_latex"] if row else None
    after = edited if (action == "edit" and edited.strip()) else None
    fid = FB.record_feedback(c, "problem", pid, action, reason=reason or None,
                             before=before, after=after)
    if action == "edit" and edited.strip():
        c.execute("UPDATE problems SET statement_latex=?, canonical_hash=? WHERE id=?",
                  (edited, canonical_hash(edited), pid))
    elif action == "reject":
        c.execute("UPDATE problems SET status='flagged' WHERE id=?", (pid,))
    rule_note = ""
    if reason.strip():
        rid, cat = FB.add_rule(c, reason.strip(), None, derived_from=fid)
        rule_note = f" · quy tắc #{rid} [{cat}]"
    c.commit(); c.close()
    return HTMLResponse(f"<div class='ok'>Đã ghi feedback #{fid} ({esc(action)}){esc(rule_note)}.</div>")


@app.get("/chuyende", response_class=HTMLResponse)
def chuyende():
    c = conn(); rows = Q.list_chuyende(c); c.close()
    trs = "".join(
        f"<tr><td>{r['id']}</td><td>{esc(r['title'])}</td><td>{esc(r['grade'])}</td>"
        f"<td><span class='pill {esc(r['status'])}'>{esc(r['status'])}</span></td>"
        f"<td>{'<a class=lk href=/pdf/'+str(r['id'])+'>PDF</a>' if r['pdf_path'] else '<span class=muted>—</span>'}</td>"
        f"<td><a class='lk' href='/chuyende/{r['id']}'>mở →</a></td></tr>" for r in rows)
    return page(f"<h2>Chuyên đề ({len(rows)})</h2><table>"
                f"<tr><th>ID</th><th>Tiêu đề</th><th>Lớp</th><th>Trạng thái</th><th>File</th><th></th></tr>"
                f"{trs}</table>")


@app.get("/chuyende/{cid}", response_class=HTMLResponse)
def chuyende_detail(cid: int):
    c = conn(); cd, members, ver = Q.get_chuyende(c, cid); c.close()
    if not cd:
        return page("<p>Không tìm thấy chuyên đề.</p>")
    mem = "".join(f"<tr><td>{m['position']}</td><td>{esc(m['role'])}</td>"
                  f"<td><a class='lk' href='/problems/{m['id']}'>#{m['id']}</a></td>"
                  f"<td class='muted'>{esc(m['s'])}…</td></tr>" for m in members)
    pdf = (f"<a class='lk' href='/pdf/{cid}'>Mở PDF</a>" if (ver and ver[0]['pdf_path']) else
           "<span class='muted'>chưa có PDF</span>")
    form = f"""<form class="fb" hx-post="/chuyende/{cid}/feedback" hx-target="#fbc{cid}">
 <label>Hành động</label>
 <select name="action"><option value="approve">approve — xuất bản</option>
  <option value="reject">reject</option><option value="rewrite">rewrite — viết lại</option></select>
 <label>Lý do (chưng cất thành quy tắc)</label>
 <textarea name="reason" placeholder="vd: thêm phần phân tích hướng giải…"></textarea>
 <button type="submit">Ghi feedback</button><div id="fbc{cid}"></div></form>"""
    return page(
        f"<h2>{esc(cd['title'])} <span class='pill {esc(cd['status'])}'>{esc(cd['status'])}</span></h2>"
        f"<p class='muted'>Lớp {esc(cd['grade'])} · v{cd['current_version']} · {pdf}</p>"
        f"<table><tr><th>#</th><th>Vai trò</th><th>Bài</th><th>Trích</th></tr>{mem}</table>"
        f"{form}<p style='margin-top:16px'><a class='lk' href='/chuyende'>← danh sách</a></p>")


@app.post("/chuyende/{cid}/feedback", response_class=HTMLResponse)
def chuyende_feedback(cid: int, action: str = Form(...), reason: str = Form("")):
    c = conn()
    fid = FB.record_feedback(c, "chuyen_de", cid, action, reason=reason or None)
    new_status = {"approve": "published", "reject": "needs_rewrite",
                  "rewrite": "needs_rewrite"}.get(action)
    if new_status:
        c.execute("UPDATE chuyen_de SET status=? WHERE id=?", (new_status, cid))
    note = ""
    if reason.strip():
        rid, cat = FB.add_rule(c, reason.strip(), None, derived_from=fid)
        note = f" · quy tắc #{rid} [{cat}]"
    c.commit(); c.close()
    return HTMLResponse(
        f"<div class='ok'>Đã ghi feedback #{fid} ({esc(action)}) → trạng thái "
        f"<b>{esc(new_status or '(giữ nguyên)')}</b>{esc(note)}.</div>")


@app.get("/rules", response_class=HTMLResponse)
def rules():
    c = conn(); rows = Q.active_rules(c); c.close()
    trs = "".join(f"<tr><td>#{r['id']}</td><td><span class='tag'>{esc(r['category'])}</span></td>"
                  f"<td>{esc(r['rule_text'])}</td></tr>" for r in rows) or \
        "<tr><td colspan=3 class='muted'>Chưa có quy tắc.</td></tr>"
    return page("<h2>Quy tắc rút ra từ phản hồi</h2>"
                "<p class='muted'>Các quy tắc active được bơm vào prompt compose.</p>"
                f"<table><tr><th>ID</th><th>Loại</th><th>Nội dung</th></tr>{trs}</table>")


@app.get("/pdf/{cid}")
def pdf(cid: int):
    c = conn()
    row = c.execute(
        "SELECT pdf_path FROM chuyen_de_versions WHERE chuyen_de_id=? ORDER BY version DESC LIMIT 1",
        (cid,)).fetchone()
    c.close()
    if row and row["pdf_path"] and os.path.exists(row["pdf_path"]):
        return FileResponse(row["pdf_path"], media_type="application/pdf")
    return HTMLResponse("<p>PDF chưa tồn tại.</p>", status_code=404)


# --- auto-resume ingest on dashboard startup (handles process interruption) ---
def _auto_resume():
    try:
        c = conn()
        pend = c.execute(
            "SELECT COUNT(*) n FROM jobs WHERE job_type='ingest_doc' "
            "AND status IN ('queued','retry','running')").fetchone()["n"]
        c.close()
        if pend > 0:
            print(f"[dashboard] auto-resume: còn {pend} job -> khởi động supervisor", flush=True)
            _launch("ingest (OCR, tự khởi động lại)", _supervise_job)
    except Exception as e:
        print("[dashboard] auto-resume lỗi:", e, flush=True)


_auto_resume()
