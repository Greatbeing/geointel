#!/usr/bin/env python3
"""store.py — SQLite 存储层：条目、FTS 检索、统计。

零依赖，只用标准库。数据库默认 /data/workspace/geointel/data/intel.db。
"""
import os
import sqlite3
from datetime import datetime, timedelta, timezone

DB = os.environ.get("GEOINTEL_DB", "/data/workspace/geointel/data/intel.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id        TEXT PRIMARY KEY,
  source    TEXT NOT NULL,
  region    TEXT,
  category  TEXT,
  title     TEXT NOT NULL,
  link      TEXT,
  summary   TEXT,
  published TEXT,
  fetched   TEXT NOT NULL,
  weight    REAL DEFAULT 1.0
);
CREATE INDEX IF NOT EXISTS idx_items_published ON items(published);
CREATE INDEX IF NOT EXISTS idx_items_category  ON items(category);
CREATE INDEX IF NOT EXISTS idx_items_source    ON items(source);
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts
  USING fts5(id UNINDEXED, title, summary, tokenize='unicode61');
"""


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def upsert_many(items):
    """items: list of dict(id, source, region, category, title, link, summary, published, weight)"""
    con = connect()
    fetched = iso(now_utc())
    new = 0
    with con:
        for it in items:
            cur = con.execute(
                "INSERT OR IGNORE INTO items"
                "(id,source,region,category,title,link,summary,published,fetched,weight)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (it["id"], it["source"], it.get("region", ""), it.get("category", ""),
                 it["title"], it.get("link", ""), it.get("summary", ""),
                 it.get("published") or fetched, fetched, float(it.get("weight", 1.0))),
            )
            if cur.rowcount:
                new += 1
                con.execute(
                    "INSERT INTO items_fts(id,title,summary) VALUES(?,?,?)",
                    (it["id"], it["title"], it.get("summary", "")),
                )
    con.close()
    return new


def recent(hours=24, limit=400):
    since = iso(now_utc() - timedelta(hours=hours))
    con = connect()
    rows = con.execute(
        "SELECT id,source,region,category,title,link,summary,published,weight"
        " FROM items WHERE published >= ? ORDER BY published DESC LIMIT ?",
        (since, limit),
    ).fetchall()
    con.close()
    return [dict(zip(("id", "source", "region", "category", "title", "link",
                      "summary", "published", "weight"), r)) for r in rows]


def search(query, limit=40, days=30):
    since = iso(now_utc() - timedelta(days=days))
    con = connect()
    terms = [t for t in "".join(c if c.isalnum() else " " for c in query).split() if len(t) > 2]
    rows = []
    if terms:
        match = " OR ".join(f'"{t}"' for t in terms[:12])
        try:
            rows = con.execute(
                "SELECT i.id,i.source,i.region,i.category,i.title,i.link,i.summary,i.published,i.weight"
                " FROM items_fts f JOIN items i ON i.id=f.id"
                " WHERE items_fts MATCH ? AND i.published >= ?"
                " ORDER BY rank LIMIT ?",
                (match, since, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
    if not rows:
        rows = con.execute(
            "SELECT id,source,region,category,title,link,summary,published,weight"
            " FROM items WHERE published >= ? ORDER BY published DESC LIMIT ?",
            (since, limit),
        ).fetchall()
    con.close()
    return [dict(zip(("id", "source", "region", "category", "title", "link",
                      "summary", "published", "weight"), r)) for r in rows]


def stats():
    con = connect()
    total = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    since = iso(now_utc() - timedelta(hours=24))
    day = con.execute("SELECT COUNT(*) FROM items WHERE published >= ?", (since,)).fetchone()[0]
    by_cat = con.execute(
        "SELECT category, COUNT(*) FROM items WHERE published >= ? GROUP BY category"
        " ORDER BY 2 DESC", (since,)).fetchall()
    con.close()
    return {"total": total, "last_24h": day, "by_category": by_cat}
