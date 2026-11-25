#!/usr/bin/env python3
"""kb_refs.py — 外部知识库导入：术语定义层 + 主题框架层。

两层：
1. 术语（term_refs）：从 Better Quant Wiki（MIT）解析出的中英对照词条，
   带定义、公式、为什么重要、常见误区、相关问题。
2. 框架（theme_refs）：从 EcoFin 长文解析出的章节结构（只取标题层级，
   不含正文），按学科和主题组织，作为知识地图。

两者都存 SQLite（kb_refs），由 kb.py 的 build_site 一起渲染，并入检索与自动链接。
导入是幂等的：按 slug 覆盖，重复导入不会产生重复条目。
"""
import html
import json
import os
import re
import sys

import store

SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_refs (
  slug      TEXT PRIMARY KEY,
  layer     TEXT NOT NULL,          -- term | theme
  kind      TEXT,                   -- 术语分组 / 学科
  title_zh  TEXT NOT NULL,
  title_en  TEXT NOT NULL DEFAULT '',
  abbr      TEXT NOT NULL DEFAULT '',
  definition TEXT NOT NULL DEFAULT '',
  formula   TEXT NOT NULL DEFAULT '',
  why       TEXT NOT NULL DEFAULT '',
  caveat    TEXT NOT NULL DEFAULT '',
  related   TEXT NOT NULL DEFAULT '[]',   -- [slug]
  importance TEXT NOT NULL DEFAULT '',
  path      TEXT NOT NULL DEFAULT '',     -- 原文相对路径
  updated   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_refs_layer ON kb_refs(layer, kind);
"""

# Better Quant Wiki 的分组顺序（决定目录里的章节次序）
GROUP_ORDER = [
    ("probability", "概率", "Probability"),
    ("statistics", "统计推断与回归", "Statistics & Regression"),
    ("markets", "市场与微观结构", "Markets & Microstructure"),
    ("macro", "宏观、利率与政策", "Macro, Rates & Policy"),
    ("valuation", "估值与公司金融", "Valuation"),
    ("pricing", "资产定价与因子", "Asset Pricing"),
    ("derivatives", "衍生品与波动", "Derivatives & Volatility"),
    ("strategies", "策略、执行与回测", "Strategies"),
]
GROUP_ZH = {k: zh for k, zh, _en in GROUP_ORDER}
GROUP_EN = {k: en for k, _zh, en in GROUP_ORDER}

# EcoFin 长文 -> (slug, 学科名, 一句话范围)
THEME_FILES = [
    ("01-economics.md", "economics", "经济学", "资源配置、市场机制、宏观调控"),
    ("02-finance.md", "finance", "金融学", "价值配置、风险管理、金融工具"),
    ("03-politics.md", "politics", "政治经济学与制度", "政策过程、制度安排、权力博弈"),
    ("04-law.md", "law", "金融法律与交易规则", "金融合约、证券监管、交易规则"),
    ("05-accounting.md", "accounting", "会计学", "信息质量、准则对比、造假识别"),
    ("06-history.md", "history", "金融与制度演变史", "危机史、市场演变、监管演变"),
    ("07-cross-themes.md", "cross-themes", "交叉主题", "跨学科综合专题"),
    ("APP-investment.md", "investment", "投资实践", "把理论落到可执行的流程"),
]


def connect():
    con = store.connect()
    con.executescript(SCHEMA)
    return con


def _slug(s):
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "-", (s or "").strip().lower()).strip("-")[:80]


# ---------- 解析 Better Quant Wiki 的 TS 对象 ----------

FIELD_RE = re.compile(r'^\s{4}(\w+):\s*(.*?)\s*,?\s*$')
STR_START = re.compile(r'^"')
ENTRY_START = re.compile(r'^\s{2}\{\s*$')


def _read_ts_string(block_lines, i, first_value):
    """读取一个字符串字段，支持跨行的双引号拼接。返回 (值, 下一行索引)。"""
    val = first_value.strip()
    if val in ('"', '",'):
        parts, i = [], i + 1
        while i < len(block_lines):
            ln = block_lines[i].strip()
            if ln.startswith('"'):
                seg = ln[1:]
                parts.append(seg[:seg.rfind('"')] if '"' in seg else seg)
                if '"' in seg:
                    i += 1
                    break
            i += 1
        return "".join(parts), i
    if val.startswith('"'):
        val = val[1:]
        end = val.rfind('"')
        return (val[:end] if end >= 0 else val), i + 1
    return "", i + 1


def parse_ts_entries(text):
    """把一个 src/content/glossary/*.ts 里的 Concept 数组解析成 dict 列表。"""
    lines = text.split("\n")
    entries, i = [], 0
    while i < len(lines):
        if not ENTRY_START.match(lines[i]) and not lines[i].strip().startswith("{"):
            i += 1
            continue
        start = i
        block = []
        i += 1
        while i < len(lines) and not lines[i].strip().startswith("},") \
                and not lines[i].strip().startswith("}"):
            block.append(lines[i])
            i += 1
        if lines[i:i + 1] and lines[i].strip().startswith("}"):
            pass
        i += 1
        entry = {"_line": start}
        j = 0
        while j < len(block):
            m = FIELD_RE.match(block[j])
            if not m:
                j += 1
                continue
            key, rest = m.group(1), m.group(2)
            if key == "_line":
                j += 1
                continue
            if rest.strip() in ("", ","):
                # 值在下一行（TS 里长字符串常这样换行）
                if j + 1 < len(block) and block[j + 1].strip().startswith('"'):
                    val, j = _read_ts_string(block, j + 1, block[j + 1].strip())
                    entry[key] = val
                else:
                    j += 1
            elif rest.startswith('"'):
                val, j = _read_ts_string(block, j, rest)
                entry[key] = val
            elif rest.startswith("["):
                raw = rest
                while "]" not in raw and j + 1 < len(block):
                    j += 1
                    raw += block[j]
                entry[key] = re.findall(r'"([^"]*)"', raw)
                j += 1
            else:
                j += 1
        if entry.get("slug"):
            entries.append(entry)
    return entries


# ---------- 解析 EcoFin 长文的标题结构 ----------

HEAD_RE = re.compile(r'^(#{1,3})\s+(.+?)\s*$')


def parse_outline(text, max_items=200):
    """提取 Markdown 的 h1/h2/h3 结构。返回 [{"level","text","anchor"}]。"""
    out = []
    for ln in text.split("\n"):
        m = HEAD_RE.match(ln)
        if not m:
            continue
        level, title = len(m.group(1)), m.group(2).strip()
        title = re.sub(r"[*`_]", "", title)
        if not title or title in ("相关主题", "如何使用本篇"):
            continue
        out.append({"level": level, "text": title})
        if len(out) >= max_items:
            break
    return out


# ---------- 导入 ----------

def import_terms(con, files, updated=None):
    """files: {group: ts 源码文本}。返回导入条数。"""
    now = store.iso(store.now_utc())
    n = 0
    with con:
        for group, text in files.items():
            for e in parse_ts_entries(text):
                slug = "t-" + _slug(group + "-" + e.get("slug", ""))
                con.execute(
                    "INSERT OR REPLACE INTO kb_refs(slug,layer,kind,title_zh,title_en,abbr,"
                    "definition,formula,why,caveat,related,importance,path,updated)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (slug, "term", group, e.get("zh") or e.get("en") or slug,
                     e.get("en", ""), e.get("abbr", ""), e.get("definition", ""),
                     e.get("formula", ""), e.get("why", ""), e.get("caveat", ""),
                     json.dumps([], ensure_ascii=False), e.get("importance", ""),
                     "towards/" + group + ".ts", now))
                n += 1
    # 术语之间的 related 用原始 slug 表达，导入后再解析成 t-<group>-<slug>
    with con:
        rows = con.execute("SELECT slug, kind, path FROM kb_refs WHERE layer='term'").fetchall()
        by_group = {}
        for slug, group, _p in rows:
            by_group.setdefault(group, {})[slug[len("t-" + group + "-"):]] = slug
        for group, text in files.items():
            for e in parse_ts_entries(text):
                slug = "t-" + _slug(group + "-" + e.get("slug", ""))
                rel = []
                for r in e.get("related") or []:
                    for g in (group, *[x for x, _z, _y in GROUP_ORDER]):
                        tgt = by_group.get(g, {}).get(r)
                        if tgt:
                            rel.append(tgt)
                            break
                con.execute("UPDATE kb_refs SET related=? WHERE slug=?",
                            (json.dumps(list(dict.fromkeys(rel)), ensure_ascii=False), slug))
    return n


def import_themes(con, book_dir):
    """book_dir: EcoFin 的 book/ 目录。返回 (学科数, 章节数)。"""
    now = store.iso(store.now_utc())
    n_sec = 0
    with con:
        for fname, slug, zh, scope in THEME_FILES:
            path = os.path.join(book_dir, fname)
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as f:
                text = f.read()
            outline = parse_outline(text)
            sections = [o["text"] for o in outline]
            h2 = [o["text"] for o in outline if o["level"] == 2]
            con.execute(
                "INSERT OR REPLACE INTO kb_refs(slug,layer,kind,title_zh,title_en,abbr,"
                "definition,formula,why,caveat,related,importance,path,updated)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("e-" + slug, "theme", zh, zh, "", "", scope, "",
                 "；".join(h2[:8]), json.dumps(sections, ensure_ascii=False),
                 json.dumps([], ensure_ascii=False),
                 "core" if slug in ("economics", "finance", "law") else "supporting",
                 "../book/" + fname, now))
            n_sec += len(sections)
    link_themes(con)
    return len([f for f in THEME_FILES if os.path.exists(os.path.join(book_dir, f[0]))]), n_sec


# 学科 -> 对应的术语分组（主题页的「相关词条」）
THEME_GROUPS = {
    "economics": ["macro", "markets"],
    "finance": ["valuation", "pricing", "derivatives"],
    "politics": ["macro"],
    "law": ["markets"],
    "accounting": ["valuation"],
    "history": ["macro", "markets"],
    "cross-themes": ["probability", "statistics"],
    "investment": ["strategies", "pricing"],
}
THEME_RELATED_MAX = 24


def link_themes(con):
    """按学科把术语挂到主题页。核心术语优先，每页最多 THEME_RELATED_MAX 条。返回更新页数。"""
    n = 0
    with con:
        for slug, groups in THEME_GROUPS.items():
            if not con.execute("SELECT 1 FROM kb_refs WHERE slug=?", ("e-" + slug,)).fetchone():
                continue
            rel = []
            for g in groups:
                rows = con.execute(
                    "SELECT slug FROM kb_refs WHERE layer='term' AND kind=? "
                    "ORDER BY (importance='core') DESC, slug", (g,)).fetchall()
                rel.extend(r[0] for r in rows)
            rel = rel[:THEME_RELATED_MAX]
            con.execute("UPDATE kb_refs SET related=? WHERE slug=?",
                        (json.dumps(rel, ensure_ascii=False), "e-" + slug))
            n += 1
    return n


# ---------- 查询 ----------

def _row(r):
    keys = ("slug", "layer", "kind", "title_zh", "title_en", "abbr", "definition",
            "formula", "why", "caveat", "related", "importance", "path", "updated")
    d = dict(zip(keys, r))
    d["related"] = json.loads(d["related"] or "[]")
    return d


def all_refs(con=None):
    own = con is None
    if con is None:
        con = connect()
    else:
        # 可能是别的模块建的库，确保参考表存在
        con.executescript(SCHEMA)
    rows = con.execute("SELECT slug,layer,kind,title_zh,title_en,abbr,definition,formula,"
                       "why,caveat,related,importance,path,updated FROM kb_refs"
                       " ORDER BY layer, kind, title_zh").fetchall()
    try:
        con.execute("CREATE TABLE IF NOT EXISTS kb_plain (slug TEXT PRIMARY KEY, plain TEXT NOT NULL)")
        plain = dict(con.execute("SELECT slug, plain FROM kb_plain").fetchall())
    except Exception:
        plain = {}
    if own:
        con.close()
    out = [_row(r) for r in rows]
    for d in out:
        d["plain"] = plain.get(d["slug"], "")
    return out


def terms_by_group(refs):
    g = {}
    for r in refs:
        if r["layer"] == "term":
            g.setdefault(r["kind"], []).append(r)
    return g


def themes(refs):
    return [r for r in refs if r["layer"] == "theme"]


# ---------- 渲染 ----------

KATEX = "https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/"


def normalize_tex(tex):
    """库里的公式多被转义了一层（\\\\frac），KaTeX 只认单反斜杠。"""
    return (tex or "").strip().replace("\\\\", "\\")


def formula_html(tex):
    """公式块：KaTeX 排版成数学式；脚本加载失败时仍显示等宽原文。"""
    src = normalize_tex(tex)
    return (
        '<link rel="stylesheet" href="%skatex.min.css">'
        '<style>.formula{margin:14px 0 18px;padding:16px 18px;border-left:3px solid var(--accent,#9b2c1f);'
        'background:rgba(155,44,31,.05);overflow-x:auto;font-size:1.15em;text-align:center}'
        '.formula code{font-size:.85em;white-space:pre-wrap}</style>'
        '<div class="formula" data-tex="%s"><code>%s</code></div>'
        '<script defer src="%skatex.min.js" onload="document.querySelectorAll(\'.formula[data-tex]\')'
        '.forEach(function(e){try{katex.render(e.dataset.tex,e,{displayMode:true,throwOnError:false})}catch(_){}})">'
        '</script>'
    ) % (KATEX, html.escape(src, quote=True), html.escape(src), KATEX)


def term_html(r, href, titles):
    parts = ['<h2 class="kbtitle">%s</h2>' % html.escape(r["title_zh"])]
    sub = " · ".join(x for x in (GROUP_ZH.get(r["kind"], r["kind"]), r["abbr"]) if x)
    parts.append('<p class="lede">%s</p>' % html.escape(sub))
    if r["title_en"]:
        parts.append('<p class="sub">%s</p>' % html.escape(r["title_en"]))
    if r.get("plain"):
        parts.append("<h2>大白话</h2><p>%s</p>" % html.escape(r["plain"]))
    if r["definition"]:
        parts.append("<h2>定义</h2><p>%s</p>" % html.escape(r["definition"]))
    if r["formula"]:
        parts.append("<h2>公式</h2>" + formula_html(r["formula"]))
    if r["why"]:
        parts.append("<h2>为什么重要</h2><p>%s</p>" % html.escape(r["why"]))
    if r["caveat"]:
        parts.append("<h2>常见误区</h2><p>%s</p>" % html.escape(r["caveat"]))
    rel = [s for s in r["related"] if s in titles]
    if rel:
        parts.append("<h2>相关词条</h2><p>%s</p>" % " ".join(
            '<a href="%s">%s</a>' % (href("/kb/%s.html" % s), html.escape(titles[s]))
            for s in rel))
    return "".join(p for p in parts if p)


def theme_html(r, href, all_titles):
    parts = ['<p class="lede">%s · 章节结构</p>' % html.escape(r["kind"])]
    if r["definition"]:
        parts.append("<p>%s</p>" % html.escape(r["definition"]))
    try:
        sections = json.loads(r["caveat"] or "[]")
    except ValueError:
        sections = []
    if sections:
        parts.append("<h2>章节</h2><ol>%s</ol>" % "".join(
            "<li>%s</li>" % html.escape(s) for s in sections))
    hit = [s for s in r["related"] if s in all_titles]
    if hit:
        parts.append("<h2>相关术语</h2><p>%s</p>" % " ".join(
            '<a href="%s">%s</a>' % (href("/kb/%s.html" % s), html.escape(all_titles[s]))
            for s in hit[:20]))
    return "".join(parts)


def refs_index_html(refs, href):
    """知识库首页里的一张表：术语按分组，框架按学科。"""
    parts = []
    by_group = terms_by_group(refs)
    if by_group:
        parts.append("<h2>术语（中英对照）</h2>")
        for key, zh, en in GROUP_ORDER:
            items = by_group.get(key) or []
            if not items:
                continue
            parts.append("<p><b>%s</b> <span class=\"sub\">%s</span></p><p>%s</p>" % (
                html.escape(zh), html.escape(en), " ".join(
                    '<a href="%s">%s</a>' % (href("/kb/%s.html" % r["slug"]),
                                             html.escape(r["title_zh"])) for r in items)))
        for key, items in by_group.items():
            if key in GROUP_ZH:
                continue
            parts.append("<p><b>%s</b></p><p>%s</p>" % (
                html.escape(key), " ".join(
                    '<a href="%s">%s</a>' % (href("/kb/%s.html" % r["slug"]),
                                             html.escape(r["title_zh"])) for r in items)))
    th = themes(refs)
    if th:
        parts.append("<h2>主题框架</h2><ul>%s</ul>" % "".join(
            '<li><a href="%s">%s</a> — %s</li>' % (
                href("/kb/%s.html" % r["slug"]), html.escape(r["title_zh"]),
                html.escape((r["definition"] or "")[:60])) for r in th))
    return "".join(parts)


def build_site(write, page, href, titles=None):
    """写 kb/ 下的参考词条页；返回 (检索条目, 已写文件名集合)。"""
    con = connect()
    refs = all_refs(con)
    con.close()
    if not refs:
        return [], set()
    all_titles = {r["slug"]: r["title_zh"] for r in refs}
    if titles:
        all_titles.update(titles)
    search, names = [], set()
    for r in refs:
        body = (term_html(r, href, all_titles) if r["layer"] == "term"
                else theme_html(r, href, all_titles))
        text = " ".join(x for x in (r["title_zh"], r["title_en"], r["definition"],
                                    r["why"], r["caveat"], r["formula"]) if x)
        kicker = "知识库 · " + (r["kind"] if r["layer"] == "term" else "主题框架")
        write("kb/%s.html" % r["slug"], page(r["title_zh"], kicker, body, "kb", big=False))
        names.add(r["slug"] + ".html")
        search.append({"title": r["title_zh"], "kicker": "知识库",
                       "url": href("/kb/%s.html" % r["slug"]),
                       "date": r["updated"][:10], "text": text})
    return search, names


def link_table(refs, href):
    """术语名 -> 页面。中文名、英文名、缩写都收，长词优先。"""
    t = {}
    for r in refs:
        if r["layer"] != "term":
            continue
        for w in (r["title_zh"], r["title_en"], r["abbr"]):
            w = (w or "").strip()
            if len(w) >= 2:
                t.setdefault(w, href("/kb/%s.html" % r["slug"]))
    return t


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="导入外部知识库")
    ap.add_argument("--terms", help="Better Quant Wiki 的 src/content/glossary 目录")
    ap.add_argument("--book", help="EcoFin 的 book 目录")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    con = connect()
    if a.terms:
        files = {}
        for name, _zh, _en in GROUP_ORDER:
            p = os.path.join(a.terms, name + ".ts")
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    files[name] = f.read()
        print("terms imported:", import_terms(con, files))
    if a.book:
        print("themes imported: %s subjects, %s sections" % import_themes(con, a.book))
    if a.list or not (a.terms or a.book):
        refs = all_refs(con)
        print("total:", len(refs), "| term:", sum(1 for r in refs if r["layer"] == "term"),
              "| theme:", sum(1 for r in refs if r["layer"] == "theme"))
        for k, items in sorted(terms_by_group(refs).items()):
            print("  %-12s %d" % (k, len(items)))
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())