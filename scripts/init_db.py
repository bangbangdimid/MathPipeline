"""Create (or migrate) the SQLite database from db/schema.sql."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.config import load_settings, abspath  # noqa: E402
from src import db  # noqa: E402


def main():
    s = load_settings()
    db_path = abspath(s["paths"]["db"])
    schema = abspath(s["paths"]["schema"])
    conn = db.get_conn(db_path)
    db.init_db(conn, schema)
    tables = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    conn.close()
    print(f"DB ready: {db_path}")
    print(f"{len(tables)} tables: {', '.join(tables)}")


if __name__ == "__main__":
    main()
