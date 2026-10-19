"""SQLite system-of-record helpers."""
from __future__ import annotations
import sqlite3
import os
import json


def get_conn(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection, schema_path: str):
    with open(schema_path, encoding="utf-8") as fh:
        conn.executescript(fh.read())
    conn.commit()


def upsert_document(conn, **kw) -> int:
    cols = ("sha256", "pdf_path", "source_url", "title", "grade", "category",
            "doc_type", "num_pages", "source_type", "bytes")
    vals = [kw.get(c) for c in cols]
    cur = conn.execute(
        f"INSERT OR IGNORE INTO documents ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
        vals)
    if cur.lastrowid:
        return cur.lastrowid
    row = conn.execute("SELECT id FROM documents WHERE sha256=?", (kw["sha256"],)).fetchone()
    return row["id"]


def insert_page(conn, document_id, page_no, kind, image_path, text_chars,
                ocr_engine=None, ocr_status="pending", latex=None, min_conf=None,
                needs_review=0) -> int:
    cur = conn.execute(
        """INSERT OR REPLACE INTO pages
           (document_id,page_no,kind,image_path,text_chars,ocr_engine,ocr_status,
            latex,min_conf,needs_review,processed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
        (document_id, page_no, kind, image_path, text_chars, ocr_engine,
         ocr_status, latex, min_conf, needs_review))
    return cur.lastrowid


def insert_element(conn, page_id, el):
    conn.execute(
        """INSERT INTO page_elements
           (page_id,idx,el_type,bbox,content,source_engine,confidence,needs_scalpel)
           VALUES (?,?,?,?,?,?,?,?)""",
        (page_id, el["idx"], el["el_type"], json.dumps(el["bbox"]),
         el["content"], el["source_engine"], el["confidence"], el["needs_scalpel"]))
