#!/usr/bin/env python3
"""render.py -- 把 digest / decisions / alerts 渲染成静态站点。

设计取向：这是一份要读的报告，不是仪表盘。
纸感底色、衬线刊头、mono 出处边栏、发丝线分节、编号章节，概率用细线量度。
零外部依赖、零外链资源（CSS 内联）；支持 prefers-color-scheme 与打印。
"""
import html
import json
import os
import re
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
SITE = os.path.join(HERE, "docs")
# GitHub Pages 项目页根在 /geointel/，链接必须以它开头，否则会串到个人主页根目录。
BASE = os.environ.get("GEOINTEL_BASE", "/geointel")

EYEBROW = {
    "今日要点": "LEDE",
    "区域动态": "REGIONS",
    "技术与产业": "TECHNOLOGY",
    "金融与政策": "CAPITAL & POLICY",
    "值得跟踪的信号": "SIGNALS",
    "问题与边界": "SCOPE",
    "已知事实": "RECORD",
    "关键不确定性": "UNCERTAINTY",
    "情景": "SCENARIOS",
    "选项评估": "OPTIONS",
    "建议与触发条件": "RECOMMENDATION",
    "反面证据与证伪条件": "FALSIFICATION",
    "复盘日期": "REVIEW",
}

CONFIDENCE_CN = {"low": "低", "medium": "中", "high": "高"}

CSS = """
:root{
  --paper:#f7f5f0; --paper-2:#f1eee7; --ink:#17171a; --ink-2:#4b4b53;
  --ink-3:#8a8a93; --rule:#dcd7cc; --rule-2:#c6c0b2; --accent:#8c2f1e;
  --serif:"Songti SC","Source Han Serif SC","Noto Serif CJK SC","Source Han Serif CN",STSong,SimSun,Georgia,serif;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei","Noto Sans CJK SC",sans-serif;
  --mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
}
@media (prefers-color-scheme: dark){
  :root{
    --paper:#131311; --paper-2:#1a1a17; --ink:#e9e5dd; --ink-2:#b4aea3;
    --ink-3:#7d776c; --rule:#302d27; --rule-2:#443f37; --accent:#cf6a4f;
  }
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0; background:var(--paper); color:var(--ink);
  font:400 17px/1.95 var(--sans); letter-spacing:.015em;
  -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility;
}
::selection{background:var(--accent); color:var(--paper)}

.sheet{
  display:grid; grid-template-columns:132px minmax(0,1fr);
  gap:0 62px; max-width:1000px; margin:0 auto;
  padding:64px 40px 120px;
}

/* ---------- 边栏：出处与索引 ---------- */
.rail{position:sticky; top:56px; align-self:start; font-family:var(--mono)}
.rail .brand{
  display:block; font-size:11px; letter-spacing:.22em; text-transform:uppercase;
  color:var(--ink); text-decoration:none; padding:0 0 14px;
  border-bottom:1px solid var(--rule-2); margin-bottom:20px;
}
.rail dl{margin:0 0 26px; padding:0}
.rail dt{
  font-size:10px; letter-spacing:.16em; text-transform:uppercase;
  color:var(--ink-3); margin:0 0 1px;
}
.rail dd{
  margin:0 0 13px; font-size:12px; line-height:1.5;
  color:var(--ink-2); font-variant-numeric:tabular-nums;
}
.rail nav{margin:0; padding:16px 0 0; border-top:1px solid var(--rule)}
.rail nav a{
  display:block; font-size:11px; letter-spacing:.14em; text-transform:uppercase;
  color:var(--ink-3); text-decoration:none; padding:5px 0;
  transition:color .16s ease;
}
.rail nav a:hover,.rail nav a[aria-current]{color:var(--ink)}

/* ---------- 刊头 ---------- */
main{min-width:0}
.masthead{padding-bottom:30px; border-bottom:2px solid var(--ink); margin-bottom:6px}
.kicker{
  font-family:var(--mono); font-size:10.5px; letter-spacing:.24em;
  text-transform:uppercase; color:var(--ink-3); margin-bottom:16px;
}
.dateline{
  font-family:var(--serif); font-size:clamp(29px,5vw,42px); line-height:1.16;
  font-weight:600; letter-spacing:.02em; margin:0; color:var(--ink);
  font-feature-settings:"palt" 1;
}
.deck{
  margin-top:15px; font-size:12.5px; line-height:1.7; color:var(--ink-2);
  font-family:var(--mono); letter-spacing:.02em;
}
.deck span+span::before{content:" · "; color:var(--ink-3)}

/* ---------- 正文 ---------- */
.body{padding-top:34px}
.body h2{
  font-family:var(--sans); font-size:19px; line-height:1.5; font-weight:600;
  letter-spacing:.03em; margin:52px 0 6px; color:var(--ink);
  padding-top:18px; border-top:1px solid var(--rule);
}
.body h2:first-child{margin-top:0; padding-top:0; border-top:0}
.body h2 .idx{
  font-family:var(--mono); font-size:11px; font-weight:400; letter-spacing:.1em;
  color:var(--accent); margin-right:14px; vertical-align:2px;
  font-variant-numeric:tabular-nums;
}
.body h2 .eyebrow{
  display:block; font-family:var(--mono); font-size:9.5px; letter-spacing:.26em;
  text-transform:uppercase; color:var(--ink-3); font-weight:400; margin:0 0 7px;
}
.body h3{
  font-size:15.5px; font-weight:600; letter-spacing:.05em; color:var(--ink-2);
  margin:30px 0 8px;
}
.body h3::before{
  content:""; display:inline-block; width:14px; height:1px;
  background:var(--rule-2); vertical-align:3px; margin-right:9px;
}
.body p{margin:0 0 1.05em; text-indent:2em; text-align:justify; text-justify:inter-ideograph}
.body p.lede{text-indent:0; font-size:16.5px; color:var(--ink-2)}
.body ul,.body ol{margin:0 0 1.15em; padding-left:1.45em}
.body li{margin:0 0 .5em; text-align:justify}
.body li::marker{color:var(--ink-3); font-family:var(--mono); font-size:12.5px}
.body ul li::marker{content:"— "}
.body strong{font-weight:600; color:var(--ink)}
.body em{font-style:normal; border-bottom:1px dashed var(--rule-2)}
.body hr{border:0; height:1px; background:var(--rule); margin:44px 0}
.body a{
  color:inherit; text-decoration:none; border-bottom:1px solid var(--rule-2);
  transition:color .16s ease,border-color .16s ease; word-break:break-word;
}
.body a:hover{color:var(--accent); border-color:var(--accent)}
.body blockquote{
  margin:26px 0; padding:2px 0 2px 20px; border-left:2px solid var(--accent);
  color:var(--ink-2); font-size:16px;
}
.body blockquote p{text-indent:0}
.body blockquote p:last-child{margin-bottom:0}
.body pre{
  margin:22px 0; padding:16px 18px; overflow:auto;
  background:var(--paper-2); border:1px solid var(--rule);
  font-family:var(--mono); font-size:13px; line-height:1.7;
}
.body code{font-family:var(--mono); font-size:.88em; background:var(--paper-2); padding:1px 4px}
.body pre code{background:none; padding:0}

/* 概率细线：情景条目末尾的（45%）下方给一条量度线 */
.body li.pct .pct-bar{
  display:block; height:2px; margin-top:8px; background:var(--rule);
  position:relative; max-width:220px;
}
.body li.pct .pct-bar::after{
  content:""; position:absolute; left:0; top:0; bottom:0;
  width:calc(var(--p) * 1%); background:var(--accent); opacity:.62;
}

/* 表格 */
.body table{
  width:100%; border-collapse:collapse; margin:26px 0;
  font-size:14px; line-height:1.72; font-variant-numeric:tabular-nums;
}
.body th{
  text-align:left; font-weight:600; font-size:10.5px; letter-spacing:.14em;
  text-transform:uppercase; color:var(--ink-3); padding:0 14px 8px 0;
  border-bottom:1px solid var(--ink); white-space:nowrap;
}
.body td{padding:10px 14px 10px 0; border-bottom:1px solid var(--rule); vertical-align:top}
.body td:last-child,.body th:last-child{padding-right:0}

/* 概率列：数字 + 一条发丝量度线，一眼看出量级 */
.body td.pct-cell{
  font-family:var(--mono); font-size:14.5px; white-space:nowrap; min-width:66px;
}
.body td.pct-cell .pct-bar{
  display:block; height:2px; margin-top:8px;
  background:var(--rule); position:relative;
}
.body td.pct-cell .pct-bar::after{
  content:""; position:absolute; left:0; top:0; bottom:0;
  width:calc(var(--p) * 1%); background:var(--accent); opacity:.65;
}

/* ---------- 列表页 ---------- */
.index-list{list-style:none; margin:0; padding:0}
.index-list li{
  display:grid; grid-template-columns:104px minmax(0,1fr); gap:0 26px;
  padding:20px 0; border-bottom:1px solid var(--rule); margin:0;
}
.index-list .when{
  font-family:var(--mono); font-size:11.5px; letter-spacing:.06em;
  color:var(--ink-3); padding-top:4px; font-variant-numeric:tabular-nums;
}
.index-list .what{font-size:17px; line-height:1.6; margin:0; text-indent:0}
.index-list .what a{border-bottom-color:transparent}
.index-list .what a:hover{border-bottom-color:var(--accent)}
.index-list .note{
  margin:7px 0 0; font-size:12.5px; line-height:1.65; color:var(--ink-3);
  font-family:var(--mono); letter-spacing:.02em; text-indent:0;
}
.index-list .note b{font-weight:400; color:var(--ink-2)}

.section-head{
  font-family:var(--mono); font-size:10px; letter-spacing:.26em; text-transform:uppercase;
  color:var(--ink-3); margin:58px 0 0; padding-bottom:10px;
  border-bottom:1px solid var(--rule-2);
}

/* ---------- 页脚 ---------- */
.colophon{
  margin-top:76px; padding-top:18px; border-top:1px solid var(--rule);
  font-family:var(--mono); font-size:10.5px; letter-spacing:.1em;
  color:var(--ink-3); display:flex; flex-wrap:wrap; gap:6px 22px;
  text-transform:uppercase;
}

/* ---------- 窄屏 ---------- */
@media (max-width:880px){
  .sheet{grid-template-columns:minmax(0,1fr); gap:0; padding:40px 22px 80px}
  .rail{position:static; margin-bottom:34px}
  .rail .brand{border-bottom:0; padding-bottom:0; margin-bottom:16px}
  .rail dl{display:flex; flex-wrap:wrap; gap:0 26px; margin-bottom:4px}
  .rail dt{font-size:9px}
  .rail dd{margin-bottom:10px}
  .rail nav{border-top:0; padding-top:0; display:flex; gap:20px; flex-wrap:wrap}
  .rail nav a{padding:0}
  body{font-size:16.5px; line-height:1.9}
  .body p{text-align:left}
  .index-list li{grid-template-columns:minmax(0,1fr); gap:5px}
  .index-list .when{padding-top:0}
  .masthead{padding-bottom:24px}
  .body table{font-size:13px}
  .body th{white-space:normal}
  .body td,.body th{padding-right:10px}
}

@media (max-width:560px){
  .body table{font-size:12.5px}
  .body td,.body th{padding-right:8px}
  .body td.pct-cell{min-width:52px; font-size:13px}
}

@media print{
  :root{--paper:#fff; --ink:#000; --ink-2:#333; --ink-3:#666; --rule:#ccc; --rule-2:#999; --accent:#000}
  .rail nav,.colophon{display:none}
  .sheet{display:block; max-width:none; padding:0}
  .body h2{break-after:avoid}
  .body li,.body p{orphans:2; widows:2}
  a{text-decoration:none}
}
"""

PCT_RE = re.compile(r"[（(](\d{1,3})\s*[%％][）)]\s*$")


def inline(s):
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2" rel="nofollow">\1</a>', s)
    s = re.sub(r"(?<![\"=>])(https?://[^\s<）)]+)", r'<a href="\1" rel="nofollow">\1</a>', s)
    return s


def heading_parts(raw):
    """'1. 问题与边界' -> ('01', '问题与边界')"""
    m = re.match(r"^(\d{1,2})\s*[.、)]\s*(.+)$", raw)
    if m:
        return m.group(1).zfill(2), m.group(2)
    return None, raw


def md2html(text, demote=False):
    """极简 markdown -> html。demote=True 时 h2 降为 h3（告警页用）。"""
    text = html.escape(text)
    lines, out, in_code, code = [], [], False, []
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            if in_code:
                out.append("<pre><code>" + "\n".join(code) + "</code></pre>")
                code, in_code = [], False
            else:
                in_code = True
            continue
        if in_code:
            code.append(line)
            continue
        lines.append(line)
    if in_code:
        out.append("<pre><code>" + "\n".join(code) + "</code></pre>")

    body, buf, list_mode, auto = [], [], None, 0

    def flush():
        nonlocal buf
        if buf:
            body.append("<p>" + inline(" ".join(buf)) + "</p>")
            buf = []

    def close_list():
        nonlocal list_mode
        if list_mode:
            body.append("</%s>" % list_mode)
            list_mode = None

    i = 0
    while i < len(lines):
        s = lines[i].strip()

        # 表格：表头行 + 分隔行
        if s.startswith("|") and i + 1 < len(lines) and \
                re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            flush()
            close_list()
            cells = [c.strip() for c in s.strip("|").split("|")]
            rows, j = [], i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            pct_col = next((k for k, c in enumerate(cells) if "概率" in c), None)
            tbl = ["<table><thead><tr>"]
            tbl += ["<th>%s</th>" % inline(c) for c in cells]
            tbl.append("</tr></thead><tbody>")
            for r in rows:
                tds = []
                for k, c in enumerate(r):
                    num = re.search(r"(\d{1,3})", c) if k == pct_col else None
                    if num:
                        tds.append('<td class="pct-cell">%s<span class="pct-bar" '
                                   'style="--p:%s"></span></td>' % (inline(c), num.group(1)))
                    else:
                        tds.append("<td>%s</td>" % inline(c))
                tbl.append("<tr>" + "".join(tds) + "</tr>")
            tbl.append("</tbody></table>")
            body.append("".join(tbl))
            i = j
            continue

        if not s:
            flush()
            close_list()
            i += 1
            continue

        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:
            flush()
            close_list()
            level = len(m.group(1))
            if level == 1:
                i += 1
                continue
            if level == 2 and not demote:
                num, label = heading_parts(m.group(2))
                if num is None:
                    auto += 1
                    num = "%02d" % auto
                parts = ['<h2><span class="idx">%s</span>' % num]
                eyebrow = EYEBROW.get(label.strip())
                if eyebrow:
                    parts.append('<span class="eyebrow">%s</span>' % eyebrow)
                parts.append(inline(label))
                parts.append("</h2>")
                body.append("".join(parts))
            else:
                body.append("<h3>%s</h3>" % inline(m.group(2)))
            i += 1
            continue

        m = re.match(r"^[-*]\s+(.*)$", s)
        if m:
            flush()
            if list_mode != "ul":
                close_list()
                body.append("<ul>")
                list_mode = "ul"
            item = m.group(1).strip()
            pm = PCT_RE.search(item)
            if pm:
                body.append('<li class="pct">%s<span class="pct-bar" style="--p:%s"></span></li>'
                            % (inline(item), pm.group(1)))
            else:
                body.append("<li>%s</li>" % inline(item))
            i += 1
            continue

        m = re.match(r"^\d+[.)]\s+(.*)$", s)
        if m:
            flush()
            if list_mode != "ol":
                close_list()
                body.append("<ol>")
                list_mode = "ol"
            body.append("<li>%s</li>" % inline(m.group(1)))
            i += 1
            continue

        if s in ("---", "***", "___"):
            flush()
            close_list()
            body.append("<hr>")
            i += 1
            continue

        if s.startswith("&gt;"):
            flush()
            close_list()
            quote = [s[4:].strip()]
            j = i + 1
            while j < len(lines) and lines[j].strip().startswith("&gt;"):
                quote.append(lines[j].strip()[4:].strip())
                j += 1
            body.append("<blockquote><p>%s</p></blockquote>" % inline(" ".join(quote)))
            i = j
            continue

        buf.append(s)
        i += 1

    flush()
    close_list()
    return "\n".join(out + body)


def frontmatter(path):
    raw = open(path, encoding="utf-8").read()
    m = re.match(r"^---\n(.*?)\n---\n", raw, flags=re.S)
    meta, body = {}, raw
    if m:
        for line in m.group(1).split("\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        body = raw[m.end():]
    return meta, body


def strip_lede(body):
    """去掉正文开头的 h1、紧随的加粗日期行和分隔线——刊头已经承担了这些信息。"""
    lines = body.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].startswith("# "):
        i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i < len(lines) and re.match(r"^\*\*.+\*\*$", lines[i].strip()):
            i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1
        if i < len(lines) and lines[i].strip() in ("---", "***", "___"):
            i += 1
    return "\n".join(lines[i:])


def cn_date(day):
    try:
        d = datetime.strptime(day, "%Y-%m-%d")
        return "%d 年 %d 月 %d 日" % (d.year, d.month, d.day)
    except ValueError:
        return day


def utc_time(stamp):
    m = re.match(r"^(\d{4}-\d\d-\d\d)T(\d\d:\d\d)", stamp or "")
    return m.group(2) + " UTC" if m else "—"


def href(path):
    return BASE + path


def page(kicker, title, rail, deck, body, current=""):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rail_html = ["<dl>"]
    for k, v in rail:
        rail_html.append("<dt>%s</dt><dd>%s</dd>" % (html.escape(str(k)), html.escape(str(v))))
    rail_html.append("</dl>")
    navs = []
    for label, path, key in (("简报", "/index.html", "digest"),
                             ("决策备忘录", "/decisions.html", "decisions"),
                             ("触发告警", "/alerts.html", "alerts")):
        cur = ' aria-current="page"' if key == current else ""
        navs.append('<a href="%s"%s>%s</a>' % (href(path), cur, label))
    deck_html = ""
    if deck:
        deck_html = '<div class="deck">%s</div>' % "".join(
            "<span>%s</span>" % html.escape(str(d)) for d in deck)

    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>{title}</title><style>{css}</style></head><body>"
        '<div class="sheet"><aside class="rail">'
        '<a class="brand" href="{home}">geointel</a>{rail}<nav>{nav}</nav>'
        '</aside><main><header class="masthead">'
        '<div class="kicker">{kicker}</div>'
        '<h1 class="dateline">{dateline}</h1>{deck}</header>'
        '<div class="body">{body}</div>'
        '<footer class="colophon"><span>只基于抓取材料</span>'
        "<span>magpie / group</span><span>{now}</span></footer>"
        "</main></div></body></html>"
    ).format(
        title=html.escape(title), css=CSS, home=href("/index.html"),
        rail="".join(rail_html), nav="".join(navs),
        kicker=html.escape(kicker), dateline=html.escape(title),
        deck=deck_html, body=body, now=now,
    )


def read_dir(sub):
    d = os.path.join(OUT, sub)
    if not os.path.isdir(d):
        return []
    return sorted((os.path.join(d, f) for f in os.listdir(d) if f.endswith(".md")), reverse=True)


def write(rel, content):
    path = os.path.join(SITE, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").write(content)


def load_json(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}


def dash(v):
    return str(v) if v not in (None, "", 0) else "—"


def digest_rail(meta, day):
    return [
        ("日期", day),
        ("生成", utc_time(meta.get("generated_at", ""))),
        ("选取", dash(meta.get("items_selected")) + " 条"),
        ("窗口", dash(meta.get("items_in_window")) + " 条"),
        ("库存", dash(meta.get("db_total")) + " 条"),
        ("源", meta.get("sources_ok") or "—"),
    ]


def digest_deck(meta):
    deck = []
    if meta.get("items_in_window"):
        deck.append("%s 条入库" % meta["items_in_window"])
    if meta.get("items_selected"):
        deck.append("%s 条入选" % meta["items_selected"])
    if meta.get("sources_ok"):
        deck.append("%s 源可用" % meta["sources_ok"])
    return deck


def build():
    os.makedirs(SITE, exist_ok=True)
    digests = read_dir("digest")
    decisions = read_dir("decisions")
    alerts = read_dir("alerts")

    digest_entries = []
    for path in digests:
        day = os.path.splitext(os.path.basename(path))[0]
        meta, body = frontmatter(path)
        write(os.path.join("digest", day + ".html"),
              page("GEOINTEL · 每日情报简报", cn_date(day),
                   digest_rail(meta, day), digest_deck(meta),
                   md2html(strip_lede(body)), "digest"))
        digest_entries.append((day, meta))

    decision_entries = []
    for path in decisions:
        name = os.path.splitext(os.path.basename(path))[0]
        meta, body = frontmatter(path)
        extra = load_json(path[:-3] + ".json")
        question = meta.get("question") or extra.get("question") or name
        day = name[:10] if re.match(r"^\d{4}-\d\d-\d\d", name) else ""
        conf_raw = str(extra.get("confidence") or "").strip()
        conf = CONFIDENCE_CN.get(conf_raw.lower(), dash(conf_raw))
        rail = [
            ("日期", day or "—"),
            ("生成", utc_time(meta.get("generated_at", ""))),
            ("依据", dash(meta.get("items_used")) + " 条"),
            ("视界", dash(meta.get("horizon_days") or extra.get("horizon_days")) + " 天"),
            ("置信", conf),
            ("复盘", extra.get("review_date") or "—"),
        ]
        deck = []
        if conf_raw:
            deck.append("%s 置信" % conf)
        if extra.get("review_date"):
            deck.append("复盘 %s" % extra["review_date"])
        if meta.get("items_used"):
            deck.append("%s 条依据" % meta["items_used"])
        write(os.path.join("decisions", name + ".html"),
              page("GEOINTEL · 决策备忘录", question, rail, deck,
                   md2html(strip_lede(body)), "decisions"))
        decision_entries.append((name, question, day, extra, conf))

    # 决策索引
    rows = []
    for name, question, day, extra, conf in decision_entries:
        note = []
        if conf != "—":
            note.append("置信 <b>%s</b>" % html.escape(conf))
        scen = extra.get("scenarios") or []
        if scen:
            top = max(scen, key=lambda s: s.get("prob") or 0)
            note.append("主情景 <b>%s %s%%</b>" % (html.escape(str(top.get("name", ""))),
                                                   top.get("prob", "")))
        if len(scen) > 1:
            note.append("%d 个情景" % len(scen))
        if extra.get("review_date"):
            note.append("复盘 <b>%s</b>" % extra["review_date"])
        rows.append(
            '<li><div class="when">%s</div><div><p class="what">'
            '<a href="%s">%s</a></p><p class="note">%s</p></div></li>'
            % (html.escape(day or "—"), href("/decisions/" + name + ".html"),
               html.escape(question), " · ".join(note) or "—"))
    write("decisions.html",
          page("GEOINTEL · 决策备忘录", "决策备忘录",
               [("条目", len(decision_entries))], ["情景概率 · 触发条件 · 证伪条件"],
               '<p class="lede">每个条目只回答一个问题：边界、已知事实、关键不确定性、'
               '情景与概率、选项评估、建议动作与触发条件、反面证据、复盘日期。</p>'
               '<ul class="index-list">' + ("".join(rows) or "<li>暂无</li>") + "</ul>",
               "decisions"))

    # 告警
    alert_html = []
    for path in alerts[:20]:
        day = os.path.splitext(os.path.basename(path))[0]
        text = open(path, encoding="utf-8").read()
        alert_html.append('<div class="section-head">%s · %s</div>' % (day, cn_date(day)))
        alert_html.append(md2html(text, demote=True))
    write("alerts.html",
          page("GEOINTEL · 触发告警", "触发告警",
               [("条目", len(alerts))], ["决策备忘录里的触发条件命中记录"],
               "".join(alert_html) or '<p class="lede">暂无触发。</p>', "alerts"))

    # 首页：最新简报 + 档案
    if digests:
        day = digest_entries[0][0]
        meta, body = frontmatter(digests[0])
        body_html = md2html(strip_lede(body))
        archive = []
        for d, _m in digest_entries[1:31]:
            archive.append('<li><div class="when">%s</div><div><p class="what">'
                           '<a href="%s">%s</a></p></div></li>'
                           % (d, href("/digest/" + d + ".html"), cn_date(d)))
        if archive:
            body_html += ('<div class="section-head">历史简报</div>'
                          '<ul class="index-list">' + "".join(archive) + "</ul>")
        recent = []
        for name, question, d, _e, conf in decision_entries[:6]:
            note = "置信 <b>%s</b>" % html.escape(conf) if conf != "—" else ""
            recent.append('<li><div class="when">%s</div><div><p class="what">'
                          '<a href="%s">%s</a></p>%s</div></li>'
                          % (html.escape(d or "—"), href("/decisions/" + name + ".html"),
                             html.escape(question),
                             '<p class="note">%s</p>' % note if note else ""))
        if recent:
            body_html += ('<div class="section-head">决策备忘录</div>'
                          '<ul class="index-list">' + "".join(recent) + "</ul>")
        write("index.html",
              page("GEOINTEL · 全球情报简报", cn_date(day),
                   digest_rail(meta, day), digest_deck(meta), body_html, "digest"))
    else:
        write("index.html",
              page("GEOINTEL · 全球情报简报", "全球情报简报",
                   [("状态", "等待首轮采集")], ["每 2 小时采集 · 每日 00:20 UTC 简报"],
                   '<p class="lede">暂无简报。</p>', "digest"))

    keep = {
        "digest": {os.path.splitext(os.path.basename(p))[0] + ".html" for p in digests},
        "decisions": {os.path.splitext(os.path.basename(p))[0] + ".html" for p in decisions},
    }
    for sub, names in keep.items():
        d = os.path.join(SITE, sub)
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if f.endswith(".html") and f not in names:
                os.remove(os.path.join(d, f))

    return {"digests": len(digests), "decisions": len(decisions), "alerts": len(alerts)}


if __name__ == "__main__":
    print(build())