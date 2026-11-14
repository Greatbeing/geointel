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
    terms = expand(query)
    rows = []
    if terms:
        match = " OR ".join(f'"{t}"' for t in terms[:16])
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
    if len(rows) < limit:
        # 语料是英文，中文问题常常检索不到；补一批「每来源最多 3 条」的近期高权重条目，
        # 保证材料在来源与题材上有覆盖，而不是被单一来源刷满。
        have = {r[0] for r in rows}
        per_source, extra = {}, []
        for r in con.execute(
            "SELECT id,source,region,category,title,link,summary,published,weight"
            " FROM items WHERE published >= ? ORDER BY weight DESC, published DESC LIMIT ?",
            (since, limit * 8),
        ):
            if r[0] in have or per_source.get(r[1], 0) >= 3:
                continue
            per_source[r[1]] = per_source.get(r[1], 0) + 1
            extra.append(r)
            if len(rows) + len(extra) >= limit:
                break
        rows = list(rows) + extra
    con.close()
    return [dict(zip(("id", "source", "region", "category", "title", "link",
                      "summary", "published", "weight"), r)) for r in rows]


# 中文决策问题 → 英文检索词。语料全是英文 RSS，没有这层映射，中文问题在 FTS 里
# 几乎必然零命中，只能退化成「最近 40 条」，材料会严重偏科。
LEXICON = {
    "半导体": ["semiconductor", "chip", "chips", "foundry"],
    "芯片": ["chip", "chips", "semiconductor"],
    "台积电": ["TSMC", "Taiwan Semiconductor"],
    "晶圆": ["wafer", "foundry"],
    "代工": ["foundry"],
    "亚利桑那": ["Arizona"],
    "产能": ["capacity", "production"],
    "爬坡": ["ramp", "ramp-up", "ramp up"],
    "良率": ["yield"],
    "出口管制": ["export control", "export controls", "entity list", "restriction"],
    "管制": ["control", "restriction", "sanction"],
    "关税": ["tariff", "tariffs", "trade war"],
    "贸易": ["trade", "tariff", "export"],
    "稀土": ["rare earth", "rare earths", "critical minerals"],
    "制裁": ["sanction", "sanctions"],
    "供应链": ["supply chain", "supply chains"],
    "能源": ["energy", "oil", "gas", "LNG"],
    "石油": ["oil", "crude", "OPEC"],
    "天然气": ["gas", "LNG"],
    "通胀": ["inflation", "CPI"],
    "利率": ["interest rate", "rates", "central bank"],
    "央行": ["central bank", "Fed", "ECB", "PBOC"],
    "降息": ["rate cut", "easing"],
    "加息": ["rate hike", "tightening"],
    "汇率": ["currency", "exchange rate", "yuan", "dollar"],
    "债务": ["debt", "bond", "yield"],
    "股市": ["stocks", "equities", "market"],
    "房地产": ["property", "real estate", "housing"],
    "人工智能": ["AI", "artificial intelligence"],
    "大模型": ["AI model", "LLM", "artificial intelligence"],
    "算力": ["compute", "data center", "GPU"],
    "数据中心": ["data center", "datacenter"],
    "台海": ["Taiwan", "Taiwan Strait"],
    "台湾": ["Taiwan"],
    "中国": ["China", "Chinese", "Beijing"],
    "美国": ["US", "United States", "Washington"],
    "欧洲": ["Europe", "EU", "European"],
    "日本": ["Japan", "Japanese", "Tokyo"],
    "印度": ["India", "Indian"],
    "俄乌": ["Ukraine", "Russia", "Putin"],
    "俄罗斯": ["Russia", "Putin", "Kremlin"],
    "乌克兰": ["Ukraine", "Kyiv"],
    "中东": ["Middle East", "Israel", "Iran"],
    "伊朗": ["Iran", "Tehran"],
    "以色列": ["Israel", "Netanyahu", "Gaza"],
    "选举": ["election", "vote", "electoral"],
    "监管": ["regulator", "regulation", "antitrust"],
    "移民": ["immigration", "migrant", "border"],
    "气候": ["climate", "emissions", "COP"],
    "战争": ["war", "conflict", "military"],
    "军费": ["defense spending", "military budget"],
    "网络安全": ["cyber", "cybersecurity", "hack"],
    "数据": ["data", "privacy"],
    "汽车": ["auto", "car", "EV"],
    "电池": ["battery", "EV", "lithium"],
    "航运": ["shipping", "freight", "Red Sea"],
}


def expand(query):
    """把中文问题扩成英文检索词；英文原词照留。"""
    out = []
    for t in "".join(c if c.isalnum() else " " for c in query).split():
        if len(t) > 2 and not t.isdigit():
            out.append(t)
    for zh, ens in LEXICON.items():
        if zh in query:
            out.extend(ens)
    seen, uniq = set(), []
    for t in out:
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(t)
    return uniq


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
