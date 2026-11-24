#!/usr/bin/env python3
"""kb.py — 知识库：机构 / 指标 / 术语三类词条，云端每日自动维护。

核心原则：正确比全面重要。
- 模型只负责「从给定材料里抽取事实」，每条事实必须引用材料 ID（条目 ID 或备忘录名）。
- 代码校验：引用的 ID 必须存在；事实里的数字、年份、日期必须在被引用材料原文里出现；
  不通过的事实直接丢弃，一条事实都不剩的词条不入库。
- 已有词条只做增量合并，每次修改写入 kb_revisions；涉及数值的修改或改动过大标记「待复核」。
- 「待复核」词条照常上线，页面显示标记。

用法：
  python3 kb.py update [--max-new 10] [--days 2]   # 每日维护（run-decide.sh 调用）
  python3 kb.py list
  python3 kb.py reviewed <slug>                    # 人工复核通过
"""
import argparse
import glob
import html
import json
import os
import re
import sys

import store

KINDS = {"org": "机构", "indicator": "指标", "term": "术语"}
MAX_NEW_PER_DAY = 10
REVIEW_CHANGE_RATIO = 0.5
OUT = os.environ.get("GEOINTEL_OUT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_entries (
  slug     TEXT PRIMARY KEY,
  kind     TEXT NOT NULL,
  title    TEXT NOT NULL,
  aliases  TEXT NOT NULL DEFAULT '[]',
  points   TEXT NOT NULL DEFAULT '[]',
  facts    TEXT NOT NULL DEFAULT '[]',
  fields   TEXT NOT NULL DEFAULT '{}',
  related  TEXT NOT NULL DEFAULT '[]',
  status   TEXT NOT NULL DEFAULT 'ok',
  created  TEXT NOT NULL,
  updated  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kb_revisions (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  slug    TEXT NOT NULL,
  ts      TEXT NOT NULL,
  reason  TEXT,
  before  TEXT,
  after   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_rev ON kb_revisions(slug, ts);
"""

# 指标类必须写清的口径字段：防止把环比当同比
INDICATOR_FIELDS = ("定义", "口径", "频率", "数据源")


def connect():
    con = store.connect()
    con.executescript(SCHEMA)
    return con


def slugify(title):
    s = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "-", (title or "").strip().lower()).strip("-")
    return s[:60]


# ---------- 校验 ----------

NUM_RE = re.compile(r"\d{4}-\d{1,2}(?:-\d{1,2})?|\d+(?:[.,]\d+)*")


def _norm(s):
    return re.sub(r"\s+", " ", (s or "")).replace(",", "")


def numbers_in(text):
    return [n.replace(",", "") for n in NUM_RE.findall(text or "")]


def verify_fact(fact, sources):
    """fact: {text, src:[id...]}; sources: {id: 原文}。返回 (ok, reason)。

    纯字符串比对，不靠模型：引用必须存在；事实中的每个数字/日期必须出现在至少一个被引用原文里。
    """
    text = (fact or {}).get("text", "").strip()
    src = [s.strip() for s in (fact or {}).get("src") or [] if isinstance(s, str)]
    # 模型常省略 "item:" 前缀：裸 ID 能对上材料就补全
    src = [("item:" + s) if s not in sources and ("item:" + s) in sources else s for s in src]
    if isinstance(fact, dict):
        fact["src"] = src
    if not text:
        return False, "empty"
    if not src:
        return False, "no source"
    missing = [s for s in src if s not in sources]
    if missing:
        return False, "unknown source %s" % missing[0]
    if any(not s.startswith("item:") for s in src):
        return False, "non-primary source"  # 备忘录是模型写的，不能当事实出处
    blob = _norm(" ".join(sources[s] for s in src))
    for n in numbers_in(text):
        if len(n) == 1:  # 单个数字（如「3 个」）误伤太多，只校验两位以上
            continue
        if n not in blob:
            return False, "number %s not in source" % n
    return True, ""


def verify_entry(draft, sources):
    """过滤草稿：去掉校验不过的事实与要点。返回 (entry 或 None, 丢弃原因列表)。"""
    kind = draft.get("kind")
    title = (draft.get("title") or "").strip()
    if kind not in KINDS or not title:
        return None, ["bad kind/title"]
    dropped = []
    facts = []
    for f in draft.get("facts") or []:
        ok, why = verify_fact(f, sources)
        if ok:
            facts.append({"text": f["text"].strip(), "src": list(dict.fromkeys(f["src"]))})
        else:
            dropped.append(why)
    if not facts:
        return None, dropped or ["no facts"]
    fact_blob = _norm(" ".join(f["text"] for f in facts))
    # 要点只能复述已校验事实里的数字
    points = []
    for p in (draft.get("points") or [])[:3]:
        p = (p or "").strip()
        if p and all(len(n) == 1 or n in fact_blob for n in numbers_in(p)):
            points.append(p)
        elif p:
            dropped.append("point number unverified")
    fields = {}
    for k, v in (draft.get("fields") or {}).items():
        v = (v or "").strip() if isinstance(v, str) else ""
        if v and all(len(n) == 1 or n in fact_blob for n in numbers_in(v)):
            fields[k] = v
    status = "ok"
    if kind != "indicator":
        fields = {}  # 职能/上级等描述无出处可核，不收
    else:
        status = "review"  # 指标口径来自模型，必须人工复核
    aliases = [a.strip() for a in draft.get("aliases") or [] if isinstance(a, str) and a.strip()][:8]
    cjk = re.compile(r"[\u4e00-\u9fff]")
    if not cjk.search(title):
        zh = next((a for a in aliases if cjk.search(a)), None)
        if zh:  # 标题强制中文，英文名进别名
            aliases = [title] + [a for a in aliases if a != zh]
            title = zh
    return {"slug": slugify(title), "kind": kind, "title": title,
            "aliases": aliases,
            "points": points, "facts": facts, "fields": fields,
            "related": [slugify(r) for r in draft.get("related") or [] if isinstance(r, str)][:8],
            "status": status}, dropped


# ---------- 存储与增量合并 ----------

def _row(con, slug):
    r = con.execute("SELECT slug,kind,title,aliases,points,facts,fields,related,status,created,updated"
                    " FROM kb_entries WHERE slug=?", (slug,)).fetchone()
    if not r:
        return None
    keys = ("slug", "kind", "title", "aliases", "points", "facts", "fields", "related",
            "status", "created", "updated")
    d = dict(zip(keys, r))
    for k in ("aliases", "points", "facts", "fields", "related"):
        d[k] = json.loads(d[k])
    return d


def all_entries(con=None):
    own = con is None
    con = con or connect()
    out = [_row(con, s) for (s,) in con.execute("SELECT slug FROM kb_entries ORDER BY title").fetchall()]
    if own:
        con.close()
    return out


def merge(old, new):
    """增量合并：保留旧事实，追加新事实（按文本去重）。返回 (merged, needs_review, reason)。"""
    if old is None:
        return new, new["status"] == "review", "create"
    seen = {f["text"] for f in old["facts"]}
    added = [f for f in new["facts"] if f["text"] not in seen]
    m = dict(old)
    m["facts"] = old["facts"] + added
    m["aliases"] = list(dict.fromkeys(old["aliases"] + new["aliases"]))[:8]
    m["related"] = list(dict.fromkeys(old["related"] + new["related"]))[:8]
    m["points"] = old["points"] or new["points"]
    fields = dict(old["fields"])
    changed_fields = []
    for k, v in new["fields"].items():
        if k not in fields:
            fields[k] = v
        elif fields[k] != v:
            changed_fields.append(k)  # 已有字段不被覆盖，冲突交给人工
    m["fields"] = fields
    review = bool(changed_fields)
    if old["facts"] and len(added) / float(len(old["facts"])) > REVIEW_CHANGE_RATIO:
        review = True
    if any(numbers_in(f["text"]) for f in added):
        review = True
    if review:
        m["status"] = "review"
    reason = "update +%d facts" % len(added)
    if changed_fields:
        reason += "; field conflict: " + ",".join(changed_fields)
    if not added and not changed_fields and m == old:
        return old, False, ""
    return m, review, reason


def save(con, entry, reason):
    now = store.iso(store.now_utc())
    old = _row(con, entry["slug"])
    created = old["created"] if old else now
    dumps = lambda v: json.dumps(v, ensure_ascii=False)  # noqa: E731
    with con:
        con.execute(
            "INSERT OR REPLACE INTO kb_entries(slug,kind,title,aliases,points,facts,fields,related,"
            "status,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (entry["slug"], entry["kind"], entry["title"], dumps(entry["aliases"]),
             dumps(entry["points"]), dumps(entry["facts"]), dumps(entry["fields"]),
             dumps(entry["related"]), entry["status"], created, now))
        snap = {k: entry[k] for k in ("kind", "title", "aliases", "points", "facts", "fields",
                                       "related", "status")}
        before = None
        if old:
            before = dumps({k: old[k] for k in snap})
        con.execute("INSERT INTO kb_revisions(slug,ts,reason,before,after) VALUES(?,?,?,?,?)",
                    (entry["slug"], now, reason, before, dumps(snap)))


def revisions(con, slug):
    return con.execute("SELECT ts,reason FROM kb_revisions WHERE slug=? ORDER BY id", (slug,)).fetchall()


def mark_reviewed(slug):
    con = connect()
    e = _row(con, slug)
    if e:
        e["status"] = "ok"
        save(con, e, "reviewed")
    con.close()
    return bool(e)


# ---------- 材料与模型 ----------

def load_sources(days=2, limit=60):
    """近期条目 + 最近备忘录，返回 {id: 原文}。"""
    src = {}
    for it in store.recent(hours=24 * days, limit=limit):
        if isinstance(it, dict):
            iid = it.get("id")
            text = "%s\n%s" % (it.get("title", ""), it.get("summary", ""))
        else:
            iid = it[0]
            text = " ".join(str(x) for x in it[1:] if x)
        src["item:" + str(iid)] = text
    memos = []  # 只用一手新闻条目；备忘录是模型产出，循环引用不可靠
    for p in memos:
        with open(p, encoding="utf-8") as f:
            src["memo:" + os.path.splitext(os.path.basename(p))[0]] = f.read()[:12000]
    return src


PROMPT = """你在维护一个地缘政治与宏观情报知识库。只允许使用下面给出的材料，不得使用材料以外的知识。

已有词条（不要重复创建，可以给它们补充新事实）：
{existing}

任务：从材料中找出最多 {n} 个值得建词条的实体，类型只能是 org（机构）、indicator（经济/市场指标）、term（术语）。
每个词条输出：
- kind, title（中文常用名，可带英文缩写）, aliases（别名/英文全称）
- facts：事实列表，每条 {{"text": 一句话, "src": [材料ID]}}。数字、日期必须与材料原文完全一致。
- points：最多 3 句要点，只复述 facts 里的内容
- fields：indicator 必须给出 定义、口径（明确写同比/环比/水平值）、频率、数据源；org 可给 职能、上级；term 可给 定义
- related：相关词条的 title
材料里没有依据的内容一律不写。宁缺毋滥。
facts 只写材料陈述的已发生事实；不要写假设、触发条件、可能性或预测。

只输出 JSON：{{"entries": [...]}}

材料：
{materials}
"""


def parse_json(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except ValueError:
        return {}


def draft_entries(sources, existing, n, chat=None):
    if chat is None:
        import llm
        chat = llm.chat
    mats = "\n\n".join("[%s]\n%s" % (k, v[:1500]) for k, v in sources.items())
    ex = "\n".join("- %s (%s)" % (e["title"], e["kind"]) for e in existing) or "（无）"
    out = chat(PROMPT.format(existing=ex, n=n, materials=mats[:60000]), max_tokens=8000)
    if isinstance(out, dict):
        out = out.get("content") or out.get("text") or ""
    return parse_json(out).get("entries") or []


def update(days=2, max_new=MAX_NEW_PER_DAY, chat=None, sources=None):
    con = connect()
    sources = sources if sources is not None else load_sources(days)
    existing = all_entries(con)
    by_slug = {e["slug"]: e for e in existing}
    by_alias = {}
    for e in existing:
        for a in [e["title"]] + e["aliases"]:
            by_alias[slugify(a)] = e["slug"]
    stats = {"created": 0, "updated": 0, "review": 0, "rejected": 0, "dropped_facts": 0}
    if not sources:
        con.close()
        return stats
    for d in draft_entries(sources, existing, max_new, chat=chat):
        entry, dropped = verify_entry(d if isinstance(d, dict) else {}, sources)
        stats["dropped_facts"] += len(dropped)
        if entry is None:
            stats["rejected"] += 1
            continue
        slug = by_alias.get(entry["slug"], entry["slug"])
        if slug not in by_slug:
            for a in entry["aliases"]:
                if slugify(a) in by_alias:
                    slug = by_alias[slugify(a)]
                    break
        entry["slug"] = slug
        old = by_slug.get(slug)
        if old is None and stats["created"] >= max_new:
            continue
        if old is not None:
            entry["kind"], entry["title"] = old["kind"], old["title"]
        merged, review, reason = merge(old, entry)
        if not reason:
            continue
        save(con, merged, reason)
        by_slug[slug] = merged
        stats["created" if old is None else "updated"] += 1
        stats["review"] += int(review)
    con.close()
    return stats


# ---------- 渲染 ----------

def source_link(sid, href, item_links):
    if sid.startswith("memo:"):
        name = sid[5:]
        return '<a href="%s">%s</a>' % (href("/decisions/%s.html" % name), html.escape(name))
    link = item_links.get(sid[5:]) if sid.startswith("item:") else ""
    if link:
        return '<a href="%s" rel="noopener">原文</a>' % html.escape(link, quote=True)
    return html.escape(sid)


def _item_links(con, entries):
    ids = {s[5:] for e in entries for f in e["facts"] for s in f["src"] if s.startswith("item:")}
    out = {}
    for i in ids:
        r = con.execute("SELECT link FROM items WHERE id=?", (i,)).fetchone()
        if r and r[0]:
            out[i] = r[0]
    return out


def entry_html(e, href, item_links, titles, revs):
    badge = ' <span class="kb-review" style="font-size:12px;padding:1px 6px;border:1px solid #b45309;color:#b45309;border-radius:3px">待复核</span>' if e["status"] == "review" else ""
    parts = ['<p class="lede">%s%s</p>' % (html.escape(KINDS[e["kind"]]), badge)]
    if e["aliases"]:
        parts.append("<p>别名：%s</p>" % html.escape("、".join(e["aliases"])))
    if e["points"]:
        parts.append("<h2>要点</h2><ol>%s</ol>" % "".join("<li>%s</li>" % html.escape(p) for p in e["points"]))
    if e["fields"]:
        parts.append("<h2>%s</h2><table>%s</table>" % (
            "口径" if e["kind"] == "indicator" else "概览",
            "".join("<tr><th>%s</th><td>%s</td></tr>" % (html.escape(k), html.escape(v))
                    for k, v in e["fields"].items())))
    parts.append("<h2>事实与出处</h2><ul>%s</ul>" % "".join(
        "<li>%s <small>[%s]</small></li>" % (
            html.escape(f["text"]), "；".join(source_link(s, href, item_links) for s in f["src"]))
        for f in e["facts"]))
    rel = [r for r in e["related"] if r in titles and r != e["slug"]]
    if rel:
        parts.append("<h2>相关词条</h2><p>%s</p>" % " ".join(
            '<a href="%s">%s</a>' % (href("/kb/%s.html" % r), html.escape(titles[r])) for r in rel))
    if revs:
        parts.append("<h2>修改历史</h2><ul>%s</ul>" % "".join(
            "<li>%s %s</li>" % (html.escape(ts[:10]), html.escape(reason or "")) for ts, reason in revs))
    return "".join(parts)


def index_html(entries, href):
    parts = ['<p class="lede">每条事实都标注出处；带「待复核」标记的词条尚未经人工确认。</p>']
    for k, label in KINDS.items():
        es = sorted((e for e in entries if e["kind"] == k), key=lambda e: e["title"].lower())
        if not es:
            continue
        parts.append("<h2>%s（%d）</h2><ul>%s</ul>" % (label, len(es), "".join(
            '<li><a href="%s">%s</a>%s</li>' % (
                href("/kb/%s.html" % e["slug"]), html.escape(e["title"]),
                ' <span class="kb-review" style="font-size:12px;padding:1px 6px;border:1px solid #b45309;color:#b45309;border-radius:3px">待复核</span>' if e["status"] == "review" else "")
            for e in es)))
    if len(parts) == 1:
        parts.append("<p>知识库还没有词条。</p>")
    return "".join(parts)


def build_site(write, page, href):
    """render.build 调用：写 kb.html 与 kb/*.html，返回 (检索条目, 已写文件名集合, 链接表)。"""
    con = connect()
    entries = all_entries(con)
    titles = {e["slug"]: e["title"] for e in entries}
    links = _item_links(con, entries)
    search, names = [], set()
    for e in entries:
        body = entry_html(e, href, links, titles, revisions(con, e["slug"]))
        write("kb/%s.html" % e["slug"], page(e["title"], "知识库 · " + KINDS[e["kind"]], body, "kb", big=False))
        names.add(e["slug"] + ".html")
        search.append({"title": e["title"], "kicker": "知识库", "url": href("/kb/%s.html" % e["slug"]),
                       "date": e["updated"][:10],
                       "text": " ".join(e["aliases"] + e["points"] + list(e["fields"].values())
                                        + [f["text"] for f in e["facts"]])})
    write("kb.html", page("知识库", "机构 · 指标 · 术语", index_html(entries, href), "kb", big=False))
    con.close()
    return search, names, link_table(entries, href)


def link_table(entries, href):
    """{显示词: url}，供正文自动链接。只收 ≥2 字符的标题/别名，长词优先。"""
    t = {}
    for e in entries:
        for w in [e["title"]] + e["aliases"]:
            w = w.strip()
            if len(w) >= 2:
                t.setdefault(w, href("/kb/%s.html" % e["slug"]))
    return t


def linkify(body_html, table, limit=1):
    """在已渲染 HTML 的正文文本里，给每个词条名的首次出现加链接。不动标签内部和已有 <a>。"""
    if not table:
        return body_html
    words = sorted(table, key=len, reverse=True)
    pat = re.compile("|".join(
        (r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(w)) if re.match(r"^[A-Za-z0-9]", w) else re.escape(w)
        for w in words))
    used = {}
    out, depth = [], 0
    for tok in re.split(r"(<[^>]+>)", body_html):
        if tok.startswith("<"):
            low = tok.lower()
            if re.match(r"<(a|h1|h2|h3|code|pre)[\s>]", low):
                depth += 1
            elif re.match(r"</(a|h1|h2|h3|code|pre)>", low):
                depth = max(0, depth - 1)
            out.append(tok)
            continue
        if depth:
            out.append(tok)
            continue

        def rep(m):
            url = table[m.group(0)]
            if used.get(url, 0) >= limit:
                return m.group(0)
            used[url] = used.get(url, 0) + 1
            return '<a class="kb-link" href="%s">%s</a>' % (url, m.group(0))
        out.append(pat.sub(rep, tok))
    return "".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    u = sub.add_parser("update")
    u.add_argument("--days", type=int, default=2)
    u.add_argument("--max-new", type=int, default=MAX_NEW_PER_DAY)
    sub.add_parser("list")
    r = sub.add_parser("reviewed")
    r.add_argument("slug")
    a = ap.parse_args(argv)
    if a.cmd == "update":
        print("kb:", json.dumps(update(a.days, a.max_new), ensure_ascii=False))
    elif a.cmd == "list":
        for e in all_entries():
            print("%-10s %-6s %s" % (e["kind"], e["status"], e["title"]))
    elif a.cmd == "reviewed":
        print("ok" if mark_reviewed(a.slug) else "not found")
    else:
        ap.print_help()
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
