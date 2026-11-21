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
        match = " OR ".join(f'"{t}"' for t in terms[:24])
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
    # 国际金融组织与多边开发银行（IFI / MDB）
    "世界银行": ["World Bank", "IBRD", "IDA"],
    "世行": ["World Bank", "IBRD", "IDA"],
    "亚洲开发银行": ["Asian Development Bank", "ADB"],
    "亚行": ["ADB", "Asian Development Bank"],
    "亚投行": ["AIIB", "Asian Infrastructure Investment Bank"],
    "新开发银行": ["New Development Bank", "NDB"],
    "农发基金": ["IFAD"],
    "国际货币基金组织": ["IMF", "International Monetary Fund"],
    "基金组织": ["IMF"],
    "国际清算银行": ["BIS", "Bank for International Settlements"],
    "金融稳定理事会": ["FSB", "Financial Stability Board"],
    "多边开发银行": ["multilateral development bank", "MDB", "World Bank", "ADB", "AIIB"],
    "多边机构": ["multilateral", "MDB", "World Bank", "ADB", "IMF"],
    "国际金融组织": ["World Bank", "ADB", "AIIB", "IMF", "MDB"],
    "经合组织": ["OECD"],
    "世贸组织": ["WTO"],
    # 央行、利率与汇率
    "美联储": ["Federal Reserve", "Fed", "FOMC"],
    "欧央行": ["ECB", "European Central Bank"],
    "日央行": ["Bank of Japan", "BOJ"],
    "英央行": ["Bank of England", "BOE"],
    "人行": ["PBOC", "People's Bank of China"],
    "基准利率": ["SOFR", "EURIBOR", "policy rate", "benchmark rate"],
    "利率转换": ["interest rate conversion", "fixed rate", "floating rate", "SOFR"],
    "货币转换": ["currency conversion", "hedging", "FX"],
    "利差": ["spread", "yield spread", "interest rate differential"],
    "收益率": ["yield", "Treasury yield", "bond yield"],
    "国债": ["Treasury", "sovereign bond", "government bond"],
    "美元": ["dollar", "USD", "DXY"],
    "欧元": ["euro", "EUR", "EURIBOR"],
    "日元": ["yen", "JPY", "BOJ"],
    "人民币": ["yuan", "renminbi", "CNY", "RMB"],
    "特别提款权": ["SDR", "Special Drawing Rights"],
    "外汇": ["FX", "foreign exchange", "currency"],
    "资本流动": ["capital flows", "cross-border", "portfolio flows"],
    # 主权债务、财政与多边规则合规
    "主权担保": ["sovereign guarantee", "sovereign-backed", "sovereign loan"],
    "主权": ["sovereign", "sovereign debt", "sovereign credit"],
    "主权债务": ["sovereign debt", "public debt", "debt sustainability"],
    "债务可持续": ["debt sustainability", "DSF", "debt distress"],
    "外债": ["external debt", "foreign currency debt"],
    "外币": ["foreign currency", "FX", "dollar", "euro", "yen"],
    "转贷": ["on-lending", "sub-loan", "sovereign loan"],
    "贷款": ["loan", "lending", "financing", "credit facility"],
    "融资": ["financing", "funding", "credit"],
    "财政": ["fiscal", "public finance", "budget", "Ministry of Finance"],
    "地方债": ["local government debt", "municipal debt", "subnational debt"],
    "信用评级": ["credit rating", "sovereign rating", "Moody's", "Fitch", "S&P"],
    "环社框架": ["Environmental and Social Framework", "ESF", "safeguards"],
    "环境与社会": ["environmental and social", "safeguards", "ESF"],
    "采购": ["procurement", "bidding", "tender"],
    "合规": ["compliance", "safeguards", "governance"],
    "气候融资": ["climate finance", "green finance", "climate adaptation"],
    "绿色基建": ["green infrastructure", "sustainable infrastructure", "clean energy"],
    "绿色金融": ["green finance", "green bond", "sustainable finance"],
    "转型金融": ["transition finance", "energy transition"],
    "基础设施": ["infrastructure", "project finance"],
    "紧缩": ["tightening", "rate hike", "financial conditions"],
    "金融条件": ["financial conditions", "liquidity", "tightening"],
    "绩效评估": ["performance evaluation", "project evaluation"],
    "重组": ["debt restructuring", "restructuring"],
}


def expand(query):
    """把中文问题扩成英文检索词；保留英文原词与缩写，过滤纯中文串避免挤占 FTS 配额。"""
    out = []
    for t in "".join(c if (c.isascii() and c.isalnum()) else " " for c in query).split():
        if len(t) >= 2 and not t.isdigit():
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
