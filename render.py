#!/usr/bin/env python3
"""render.py — 把 digest / decisions / alerts 渲染成静态站点。

零外部依赖、零外链资源（CSS 内联，浏览器直接打开即可）。
根路径链接由 BASE 控制：默认 /geointel，适配 GitHub Pages 项目页；
本地双击预览时把 GEOINTEL_BASE 设为空串，或直接用相对路径。
"""
import html
import os
import re
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
SITE = os.path.join(HERE, "docs")
BASE = os.environ.get("GEOINTEL_BASE", "/geointel")

CSS = ""
"".join([
    ":root{--bg:#0f1115;--fg:#e6e6e6;--dim:#8b93a1;--line:#232833;--acc:#6ea8fe}",
    "*{box-sizing:border-box}",
    "body{margin:0;background:var(--bg);color:var(--fg);",
    "font:16px/1.75 -apple-system,Segoe UI,system-ui,sans-serif}",
    ".wrap{max-width:820px;margin:0 auto;padding:32px 20px 80px}",
    "header{border-bottom:1px solid var(--line);padding-bottom:18px;margin-bottom:28px}",
    "h1{font-size:22px;margin:0 0 6px}",
    "h2{font-size:18px;margin:32px 0 10px;color:#fff}",
    "h3{font-size:16px;margin:22px 0 8px;color:#cfd6e4}",
    "p,li{color:#d7dbe3}",
    "a{color:var(--acc);text-decoration:none}",
    "a:hover{text-decoration:underline}",
    ".meta{color:var(--dim);font-size:13px}",
    "ul{padding-left:22px}",
    "li{margin:4px 0}",
    "hr{border:0;border-top:1px solid var(--line);margin:28px 0}",
    ".nav a{margin-right:16px;font-size:14px}",
    ".card{border:1px solid var(--line);border-radius:10px;padding:16px 18px;",
    "margin:12px 0;background:#141821}",
    "pre{background:#0b0d12;border:1px solid var(--line);border-radius:8px;",
    "padding:12px;overflow:auto}",
    "code{font-family:ui-monospace,Consolas,monospace;font-size:14px}",
    "blockquote{border-left:3px solid var(--line);margin:0;padding-left:14px;color:var(--dim)}",
])


def md2html(text):
    text = html.escape(text)
    blocks, out, in_code, code = [], [], False, []
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
        blocks.append(line)
    if in_code:
        out.append("<pre><code>" + "\n".join(code) + "</code></pre>")

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

    for line in blocks:
        s = line.strip()
        if not s:
            flush()
            close_list()
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:
            flush()
            close_list()
            n = len(m.group(1))
            body.append("<h%d>%s</h%d>" % (n, inline(m.group(2)), n))
            continue
        m = re.match(r"^[-*]\s+(.*)$", s)
        if m:
            flush()
            if list_mode != "ul":
                close_list()
                body.append("<ul>")
                list_mode = "ul"
            body.append("<li>%s</li>" % inline(m.group(1)))
            continue
        m = re.match(r"^\d+[.)]\s+(.*)$", s)
        if m:
            flush()
            if list_mode != "ol":
                close_list()
                body.append("<ol>")
                list_mode = "ol"
            body.append("<li>%s</li>" % inline(m.group(1)))
            continue
        if s in ("---", "***"):
            flush()
            close_list()
            body.append("<hr>")
            continue
        buf.append(s)
    flush()
    close_list()
    return "\n".join(out + body)


def inline(s):
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2" rel="nofollow">\1</a>', s)
    return s


def href(path):
    return BASE + path


def page(title, body, subtitle=""):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    head = (
        '<!doctype html><html lang="zh"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>%s</title><style>%s</style></head>\n"
        '<body><div class="wrap"><header>\n<h1>%s</h1>\n'
        '<div class="meta">%s</div>\n<div class="nav">'
    ) % (html.escape(title), CSS, html.escape(title), html.escape(subtitle))
    links = (
        '<a href="%s">首页</a>'
        '<a href="%s">决策备忘录</a>'
        '<a href="%s">触发告警</a>'
    ) % (href("/index.html"), href("/decisions.html"), href("/alerts.html"))
    tail = (
        '</div>\n</header>%s\n<hr><div class="meta">'
        'geointel · 只基于抓取材料 · %s</div>\n</div></body></html>'
    ) % (body, now)
    return head + links + tail


def read_dir(sub):
    d = os.path.join(OUT, sub)
    if not os.path.isdir(d):
        return []
    return sorted((os.path.join(d, f) for f in os.listdir(d) if f.endswith(".md")), reverse=True)


def body_of(path):
    raw = open(path, encoding="utf-8").read()
    return re.sub(r"^---\n.*?\n---\n", "", raw, flags=re.S)


def write(rel, content):
    path = os.path.join(SITE, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").write(content)


def build():
    os.makedirs(SITE, exist_ok=True)
    digests = read_dir("digest")
    decisions = read_dir("decisions")
    alerts = read_dir("alerts")

    for path in digests:
        name = os.path.splitext(os.path.basename(path))[0]
        write(os.path.join("digest", name + ".html"),
              page("情报简报 " + name, md2html(body_of(path)), "每日全球情报摘要"))

    cards = []
    for path in decisions:
        name = os.path.splitext(os.path.basename(path))[0]
        text = body_of(path)
        first = next((l.lstrip("#").strip() for l in text.split("\n") if l.startswith("# ")), name)
        cards.append('<div class="card"><a href="%s">%s</a><div class="meta">%s</div></div>'
                     % (href("/decisions/" + name + ".html"), html.escape(first), html.escape(name)))
        write(os.path.join("decisions", name + ".html"),
              page(first, md2html(text), "决策备忘录 · " + name))
    write("decisions.html", page("决策备忘录", "".join(cards) or "<p>暂无</p>", "结构化决策分析"))

    alert_html = []
    for path in alerts[:14]:
        name = os.path.splitext(os.path.basename(path))[0]
        alert_html.append("<h2>%s</h2>%s" % (html.escape(name), md2html(body_of(path))))
    write("alerts.html", page("触发告警", "".join(alert_html) or "<p>暂无触发</p>",
                              "决策备忘录里的触发条件命中记录"))

    archive = []
    for path in digests[:30]:
        name = os.path.splitext(os.path.basename(path))[0]
        archive.append('<li><a href="%s">%s</a></li>' % (href("/digest/" + name + ".html"),
                                                          html.escape(name)))
    latest = md2html(body_of(digests[0])) if digests else "<p>暂无简报，等第一轮 digest。</p>"
    write("index.html", page("全球情报简报", latest + "<h2>历史简报</h2><ul>" +
                             ("".join(archive) or "<li>暂无</li>") + "</ul>",
                             "每日自动采集 · 只基于抓取材料"))

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
