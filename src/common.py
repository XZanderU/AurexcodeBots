
from pathlib import Path
import sqlite3
import csv

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
EXPORTS = ROOT / "exports"
CONFIG = ROOT / "config"
LOGS = ROOT / "logs"
DB_PATH = DATA / "aurexcode.db"

for folder in (DATA, EXPORTS, CONFIG, LOGS):
    folder.mkdir(parents=True, exist_ok=True)


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prospects (
            domain TEXT PRIMARY KEY,
            company_name TEXT,
            url TEXT,
            description TEXT,
            matched_keywords TEXT,
            source_url TEXT,
            first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
            last_seen TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            article_url TEXT PRIMARY KEY,
            title TEXT,
            published TEXT,
            source_name TEXT,
            categories TEXT,
            summary TEXT,
            collected_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    return conn


def read_lines(path):
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def export_table(table, filename):
    allowed = {
        "prospects": [
            "domain", "company_name", "url", "description",
            "matched_keywords", "source_url", "first_seen", "last_seen"
        ],
        "articles": [
            "article_url", "title", "published", "source_name",
            "categories", "summary", "collected_at"
        ]
    }

    if table not in allowed:
        raise ValueError("Tabla no permitida")

    output = EXPORTS / filename
    conn = connect()
    try:
        columns = allowed[table]
        rows = conn.execute(
            f"SELECT {', '.join(columns)} FROM {table}"
        ).fetchall()

        with output.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file)
            writer.writerow(columns)
            writer.writerows(rows)
    finally:
        conn.close()

    return output
