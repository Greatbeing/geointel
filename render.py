#!/usr/bin/env python3
"""render.py -- 把 digest / decisions / alerts 渲染成静态站点。

设计取向：一份给人读的报告。单栏居中、衬线标题、发丝线分节、
正文首行缩进；只保留读者需要的信息，运维数字（库存条数、生成耗时、
触发 ID、命中计数）一律不上页面。

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

CONFIDENCE_CN = {"low": "低", "medium": "中", "high": "高"}

CSS = """
:root{
  --paper:#faf9f6; --paper-2:#f3f1ec; --ink:#1a1a1c; --ink-2:#55555c;
  --ink-3:#8e8e96; --rule:#e6e2d9; --rule-2:#cfc9bc; --accent:#9a3b2a;
  --serif:"Source Han Serif SC","Noto Serif SC","Songti SC",STSong,SimSun,Georgia,serif;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei","Noto Sans CJK SC",sans-serif;
  --mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace;
}
@media (prefers-color-scheme: dark){
  :root{
    --paper:#151513; --paper-2:#1d1d1a; --ink:#e8e4dc; --ink-2:#b3ada2;
    --ink-3:#7e786e; --rule:#2e2b26; --rule-2:#433f38; --accent:#d0705a;
  }
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%; scroll-behavior:smooth}
body{
  margin:0; background:var(--paper); color:var(--ink);
  font:400 17px/1.9 var(--sans); letter-spacing:.012em;
  -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility;
}
::selection{background:var(--accent); color:var(--paper)}

.top{
  max-width:720px; margin:0 auto; padding:24px 24px 18px;
  display:flex; align-items:baseline; justify-content:space-between; gap:18px;
  border-bottom:1px solid var(--rule);
}
.top .brand{
  font-family:var(--mono); font-size:12px; letter-spacing:.2em;
  text-transform:uppercase; color:var(--ink); text-decoration:none;
}
.top nav{display:flex; gap:22px; flex-wrap:wrap}
.top nav a{
  font-size:13.5px; color:var(--ink-3); text-decoration:none;
  transition:color .16s ease;
}
.top nav a:hover{color:var(--ink)}
.top nav a[aria-current]{color:var(--ink)}

.wrap{max-width:720px; margin:0 auto; padding:0 24px 100px}

.kicker{font-size:12.5px; color:var(--ink-3); letter-spacing:.1em; margin:46px 0 12px}
h1.dateline{
  font-family:var(--serif); font-weight:400; font-feature-settings:"palt" 1;
  font-size:clamp(27px,4.4vw,36px); line-height:1.34; letter-spacing:.02em;
  margin:0 0 10px; color:var(--ink);
}
h1.dateline.q{font-size:clamp(24px,3.6vw,31px); line-height:1.5}
.under{font-size:13px; color:var(--ink-3); margin:0 0 8px}
.under span+span::before{content:" · "; color:var(--rule-2)}
.masthead{border-bottom:1.5px solid var(--ink); padding-bottom:26px; margin-bottom:4px}

.body{padding-top:30px}
.body h2{
  font-size:18.5px; line-height:1.55; font-weight:600; letter-spacing:.02em;
  margin:46px 0 12px; padding-top:20px; color:var(--ink);
  border-top:1px solid var(--rule);
}
.body h2:first-child{margin-top:0; padding-top:0; border-top:0}
.body h3{
  font-size:15.5px; font-weight:600; letter-spacing:.03em; color:var(--ink-2);
  margin:26px 0 6px;
}
.body p{margin:0 0 .6em; text-indent:2em; text-align:justify; text-justify:inter-ideograph}
.body p.lede{text-indent:0; font-size:16.5px; color:var(--ink-2); margin-bottom:1.1em}
.body ul,.body ol{margin:0 0 1.1em; padding-left:1.5em}
.body li{margin:0 0 .4em; text-align:left}
.body .src{font-size:.84em; color:var(--ink-3); letter-spacing:0}
.body li::marker{color:var(--ink-3); font-family:var(--mono); font-size:12.5px}
.body ul li::marker{content:"— "}
.body strong{font-weight:600; color:var(--ink)}
.body em{font-style:normal; border-bottom:1px dashed var(--rule-2)}
.body hr{border:0; height:1px; background:var(--rule); margin:40px 0}
.body a{
  color:inherit; text-decoration:none; border-bottom:1px solid var(--rule-2);
  transition:color .16s ease,border-color .16s ease; word-break:break-word;
}
.body a:hover{color:var(--accent); border-color:var(--accent)}
.body blockquote{
  margin:24px 0; padding:2px 0 2px 18px; border-left:2px solid var(--accent);
  color:var(--ink-2);
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

/* 表格 */
.body table{
  width:100%; border-collapse:collapse; margin:24px 0;
  font-size:14px; line-height:1.72; font-variant-numeric:tabular-nums;
}
.body th{
  text-align:left; font-weight:600; font-size:12px; color:var(--ink-3);
  padding:0 14px 8px 0; border-bottom:1px solid var(--ink); white-space:nowrap;
}
.body td{padding:11px 14px 11px 0; border-bottom:1px solid var(--rule); vertical-align:top}
.body td:last-child,.body th:last-child{padding-right:0}

/* 概率列：数字 + 一条发丝量度线 */
.body td.pct-cell{font-family:var(--mono); font-size:14.5px; white-space:nowrap; min-width:66px}
.body td.pct-cell .pct-bar{display:block; height:2px; margin-top:8px; background:var(--rule); position:relative}
.body td.pct-cell .pct-bar::after{
  content:""; position:absolute; left:0; top:0; bottom:0;
  width:calc(var(--p) * 1%); background:var(--accent); opacity:.65;
}

/* 首段当导语：不缩进、略大、颜色更实 */
.body h2:first-child + p{
  text-indent:0; font-size:18px; line-height:1.85; color:var(--ink);
  margin-bottom:1.15em;
}

/* 索引列表 */
.index-list{list-style:none; margin:0; padding:0}
.index-list li{
  display:grid; grid-template-columns:96px minmax(0,1fr); gap:0 22px;
  padding:16px 0; border-bottom:1px solid var(--rule); margin:0;
}
.index-list .when{
  font-family:var(--mono); font-size:11.5px; color:var(--ink-3);
  padding-top:5px; font-variant-numeric:tabular-nums; letter-spacing:.04em;
}
.index-list .what{font-size:16.5px; line-height:1.6; margin:0; text-indent:0}
.index-list .what a{border-bottom-color:transparent}
.index-list .what a:hover{border-bottom-color:var(--accent)}
.index-list .note{
  margin:5px 0 0; font-size:13px; line-height:1.6; color:var(--ink-3); text-indent:0;
}
.index-list .note b{font-weight:400; color:var(--ink-2)}

.plain-list{list-style:none; margin:0; padding:0}
.plain-list li{padding:13px 0; border-bottom:1px solid var(--rule); margin:0; font-size:16px}
.plain-list a{color:var(--ink-2)}
.plain-list a:hover{color:var(--accent); border-color:var(--accent)}

.section-head{
  font-size:13px; color:var(--ink-3); letter-spacing:.08em;
  margin:56px 0 4px; padding-bottom:10px; border-bottom:1px solid var(--rule-2);
}

/* 告警 */
.alert-item{font-size:13px; color:var(--ink-3); margin:0 0 6px; text-indent:0}
.alert-item a{color:var(--ink-2)}

.colophon{
  margin-top:80px; padding-top:18px; border-top:1px solid var(--rule);
  font-size:12.5px; color:var(--ink-3); display:flex; flex-wrap:wrap; gap:4px 16px;
}
.colophon span+span::before{content:"· "; color:var(--rule-2)}

@media (max-width:600px){
  body{font-size:16.5px; line-height:1.88}
  .top{padding:20px 20px 14px; flex-direction:column; gap:10px}
  .top nav{gap:18px}
  .wrap{padding:0 20px 72px}
  .kicker{margin-top:34px}
  .body p{text-align:left}
  .body table{font-size:13px}
  .body th{white-space:normal}
  .body td,.body th{padding-right:10px}
  .index-list li{grid-template-columns:minmax(0,1fr); gap:3px}
  .index-list .when{padding-top:0}
}

@media (max-width:420px){
  .body table{font-size:12.5px}
  .body td,.body th{padding-right:8px}
  .body td.pct-cell{min-width:52px; font-size:13px}
}

@media print{
  :root{--paper:#fff; --ink:#000; --ink-2:#333; --ink-3:#666; --rule:#ccc; --rule-2:#999; --accent:#000}
  .top nav,.colophon{display:none}
  .top{border-bottom:none; padding:0}
  .wrap{max-width:none; padding:0}
  .body h2{break-after:avoid}
  .body li,.body p{orphans:2; widows:2}
  a{text-decoration:none}
}
"""


def inline(s):
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2" rel="nofollow">\1</a>', s)
    s = re.sub(r"(?<![\"=>])(https?://[^\s<）)]+)", r'<a href="\1" rel="nofollow">\1</a>', s)
    # 句末出处（来源、日期）压小压淡，让正文先被读到
    s = SRC_RE.sub(lambda m: '<span class="src">（%s）</span>' % m.group(1), s)
    return s


def md2html(text, demote=False):
    """极简 markdown -> html。demote=True 时 h2 降为 h3（告警页用）。"""
    text = html.escape(text)
    lines, pre, in_code, code = [], [], False, []
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            if in_code:
                pre.append("<pre><code>" + "\n".join(code) + "</code></pre>")
                code, in_code = [], False
            else:
                in_code = True
            continue
        if in_code:
            code.append(line)
            continue
        lines.append(line)
    if in_code:
        pre.append("<pre><code>" + "\n".join(code) + "</code></pre>")

    body, buf, list_mode = [], [], None

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
                body.append("<h2>%s</h2>" % inline(m.group(2)))
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
            body.append("<li>%s</li>" % inline(m.group(1).strip()))
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
    return "".join(pre + body)


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


OPS_LINE = re.compile(r"^(源状态|抓取状态|统计窗口|覆盖率)\s*[:：]")
FENCE_RE = re.compile(r"```[a-zA-Z]*\s*\n(.*?)```", re.S)
SRC_RE = re.compile(r"（([^（）]*(?:\d{4}-\d{2}-\d{2}|[A-Za-z]{2,})[^（）]*)）\s*$")


def strip_ops(text):
    """删掉生成流程留在正文里的运维行（源状态、抓取条数等），读者不需要。"""
    return "\n".join(l for l in text.split("\n") if not OPS_LINE.match(l.strip()))


def strip_data_blocks(text):
    """备忘录末尾常带一段 JSON（情景/触发条件的机读版），页面上已有正文化表达，不再重复展示。"""
    def drop(m):
        body = m.group(1).strip()
        return "\n" if body.startswith("{") or body.startswith("[") else m.group(0)
    return FENCE_RE.sub(drop, text)


def cn_date(day):
    try:
        d = datetime.strptime(day, "%Y-%m-%d")
        return "%d 年 %d 月 %d 日" % (d.year, d.month, d.day)
    except ValueError:
        return day


def href(path):
    return BASE + path


def page(title, kicker, body, current="", under=None, big=True):
    now = datetime.now(timezone.utc)
    navs = []
    for label, path, key in (("简报", "/index.html", "digest"),
                             ("决策备忘录", "/decisions.html", "decisions"),
                             ("触发告警", "/alerts.html", "alerts")):
        cur = ' aria-current="page"' if key == current else ""
        navs.append('<a href="%s"%s>%s</a>' % (href(path), cur, label))
    under_html = ""
    if under:
        under_html = '<p class="under">%s</p>' % "".join(
            "<span>%s</span>" % html.escape(str(u)) for u in under)

    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>{title}</title><style>{css}</style></head><body>"
        '<div class="top"><a class="brand" href="{home}">geointel</a>'
        "<nav>{nav}</nav></div>"
        '<div class="wrap"><header class="masthead">'
        '<p class="kicker">{kicker}</p>'
        '<h1 class="dateline{q}">{title}</h1>{under}</header>'
        '<div class="body">{body}</div>'
        '<footer class="colophon"><span>只基于抓取材料</span>'
        "<span>{updated} 更新</span></footer></div></body></html>"
    ).format(
        title=html.escape(title), css=CSS, home=href("/index.html"),
        nav="".join(navs), kicker=html.escape(kicker),
        q="" if big else " q", under=under_html, body=body,
        updated="%d 月 %d 日 %02d:%02d UTC" % (now.month, now.day, now.hour, now.minute),
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


def clean_alert(text):
    """去掉告警里的运维信息：统计窗口、触发 ID、命中计数、英文线索词、内部 slug。"""
    keep = []
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("统计窗口"):
            continue
        if re.match(r"^-\s*命中\s*\d+\s*条", s):
            continue
        m = re.match(r"^-\s*关联决策：.*?（(\d{4}-\d\d-\d\d-[a-z0-9-]+)）", s)
        if m:
            keep.append("- 关联备忘录：{{LINK:%s}}" % m.group(1))
            continue
        if re.match(r"^-\s*关联决策：", s):
            continue
        m = re.match(r"^-\s*阈值：([^；\n]+)", s)
        if m:
            keep.append("- 阈值：%s" % m.group(1).strip())
            continue
        keep.append(line)
    text = "\n".join(keep)
    text = re.sub(r"^(#{1,4})\s*\[[^\]]+\]\s*", r"\1 ", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def build():
    os.makedirs(SITE, exist_ok=True)
    digests = read_dir("digest")
    decisions = read_dir("decisions")
    alerts = read_dir("alerts")

    digest_entries = []
    for path in digests:
        day = os.path.splitext(os.path.basename(path))[0]
        _meta, body = frontmatter(path)
        write(os.path.join("digest", day + ".html"),
              page(cn_date(day), "每日情报简报", md2html(strip_ops(strip_lede(body))), "digest"))
        digest_entries.append((day, _meta))

    decision_entries = []
    for path in decisions:
        name = os.path.splitext(os.path.basename(path))[0]
        meta, body = frontmatter(path)
        extra = load_json(path[:-3] + ".json")
        question = meta.get("question") or extra.get("question") or name
        day = name[:10] if re.match(r"^\d{4}-\d\d-\d\d", name) else ""
        conf_raw = str(extra.get("confidence") or "").strip()
        conf = CONFIDENCE_CN.get(conf_raw.lower(), conf_raw)
        under = []
        if conf:
            under.append("置信度 %s" % conf)
        if extra.get("review_date"):
            under.append("复盘 %s" % extra["review_date"])
        if meta.get("horizon_days"):
            under.append("视界 %s 天" % meta["horizon_days"])
        write(os.path.join("decisions", name + ".html"),
              page(question, "决策备忘录",
                   md2html(strip_data_blocks(strip_ops(strip_lede(body)))), "decisions",
                   under=under, big=False))
        decision_entries.append({
            "name": name, "question": question, "day": day,
            "conf": conf, "extra": extra,
            "url": href("/decisions/" + name + ".html"),
        })

    # 决策索引
    rows = []
    for e in decision_entries:
        note = []
        if e["conf"]:
            note.append("置信度 %s" % html.escape(e["conf"]))
        scen = e["extra"].get("scenarios") or []
        if scen:
            top = max(scen, key=lambda s: s.get("prob") or 0)
            note.append("主情景 %s %s%%" % (html.escape(str(top.get("name", ""))), top.get("prob", "")))
        if e["extra"].get("review_date"):
            note.append("复盘 %s" % e["extra"]["review_date"])
        rows.append(
            '<li><div class="when">%s</div><div><p class="what"><a href="%s">%s</a></p>'
            '<p class="note">%s</p></div></li>'
            % (html.escape(e["day"] or "—"), e["url"], html.escape(e["question"]),
               " · ".join(note) or "—"))
    write("decisions.html",
          page("决策备忘录", "决策备忘录",
               '<p class="lede">每个条目只回答一个问题：边界、已知事实、关键不确定性、'
               '情景与概率、选项评估、建议动作与触发条件、反面证据、复盘日期。</p>'
               '<ul class="index-list">' + ("".join(rows) or "<li>暂无</li>") + "</ul>",
               "decisions"))

    # 告警：跨链到对应备忘录
    alert_chunks = []
    for path in alerts[:20]:
        day = os.path.splitext(os.path.basename(path))[0]
        text = clean_alert(open(path, encoding="utf-8").read())
        body = md2html(text, demote=True)
        body = re.sub(r"\{\{LINK:([a-z0-9-]+)\}\}",
                      lambda m: '<a href="%s">相关备忘录</a>'
                      % href("/decisions/" + m.group(1) + ".html"), body)
        alert_chunks.append("<h2>%s</h2>%s" % (cn_date(day), body))
    write("alerts.html",
          page("触发告警", "触发告警",
               "".join(alert_chunks) or '<p class="lede">暂无触发。</p>', "alerts"))

    # 首页：最新简报 + 档案
    if digests:
        day = digest_entries[0][0]
        _meta, body = frontmatter(digests[0])
        body_html = md2html(strip_ops(strip_lede(body)))
        archive = []
        for d, _m in digest_entries[1:31]:
            archive.append('<li><a href="%s">%s</a></li>'
                           % (href("/digest/" + d + ".html"), cn_date(d)))
        if archive:
            body_html += ('<div class="section-head">历史简报</div>'
                          '<ul class="plain-list">' + "".join(archive) + "</ul>")
        recent = []
        for e in decision_entries[:6]:
            note = "置信度 %s" % html.escape(e["conf"]) if e["conf"] else ""
            recent.append('<li><div class="when">%s</div><div><p class="what">'
                          '<a href="%s">%s</a></p>%s</div></li>'
                          % (html.escape(e["day"] or "—"), e["url"],
                             html.escape(e["question"]),
                             '<p class="note">%s</p>' % note if note else ""))
        if recent:
            body_html += ('<div class="section-head">决策备忘录</div>'
                          '<ul class="index-list">' + "".join(recent) + "</ul>")
        write("index.html", page(cn_date(day), "每日情报简报", body_html, "digest"))
    else:
        write("index.html", page("全球情报简报", "每日情报简报",
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