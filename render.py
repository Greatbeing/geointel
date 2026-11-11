#!/usr/bin/env python3
"""site.py — 把 digest / decisions / alerts 渲染成离线可读的静态站点。

无外部依赖、无外链资源（单文件 CSS），可以直接用浏览器打开，也可以推到 GitHub Pages。
"""
import html
import os
import re
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
SITE = os.path.join(HERE, "site")

CSS = """
:root{--bg:#0f1115;--fg:#e6e6e6;--dim:#8b93a1;--line:#232833;--acc:#6ea8fe}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.75 -apple-system,"Segoe UI",system-ui,sans-serif}
.wrap{max-width:820px;margin:0 auto;padding:32px 20px 80px}
header{border-bottom:1px solid var(--line);padding-bottom:18px;margin-bottom:28px}
h1{font-size:22px;margin:0 0 6px}
h2{font-size:18px;margin:32px 0 10px;color:#fff}
h3{font-size:16px;margin:22px 0 8px;color:#cfd6e4}
p,li{color:#d7dbe3}
a{color:var(--acc);text-decoration:none}
a:hover{text-decoration:underline}
.meta{color:var(--dim);font-size:13px}
ul{padding-left:22px}
li{margin:4px 0}
hr{border:0;border-top:1px solid var(--line);margin:28px 0}
.nav a{margin-right:16px;font-size:14px}
.card{border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:12px 0;background:#141821}
.tag{display:inline-block;font-size:12px;color:var(--dim);border:1px solid var(--line);border-radius:999px;padding:1px 8px;margin-right:6px}
pre{background:#0b0d12;border:1px solid var(--line);border-radius:8px;padding:12px;overflow:auto}
code{font-family:ui-monospace,Consolas,monospace;font-size:14px}
blockquote{border-left:3px solid var(--line);margin:0;padding-left:14px;color:var(--dim)}
"""


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
            body.append(f"</{list_mode}>")
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
            body.append(f"<h{n}>{inline(m.group(2))}</h{n}>")
            continue
        m = re.match(r"^[-*]\s+(.*)$", s)
        if m:
            flush()
            if list_mode != "ul":
                close_list()
                body.append("<ul>")
                list_mode = "ul"
            body.append(f"<li>{inline(m.group(1))}</li>")
            continue
        m = re.match(r"^\d+[.)]\s+(.*)$", s)
        if m:
            flush()
            if list_mode != "ol":
                close_list()
                body.append("<ol>")
                list_mode = "ol"
            body.append(f"<li>{inline(m.group(1))}</li>")
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
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', s)
    return s


def page(title, body, subtitle=""):
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>{CSS}</style></head>
<body><div class="wrap"><header>
<h1>{html.escape(title)}</h1>
<div class="meta">{html.escape(subtitle)}</div>
<div class="nav"><a href="/index.html">首页</a><a href="/decisions.html">决策备忘录</a><a href="/alerts.html">触发告警</a></div>
</header>{body}
<hr><div class="meta">geointel · 由 magpie/group/daily 生成 · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</div>
</div></body></html>"""


def read_dir(sub, ext=".md"):
    d = os.path.join(OUT, sub)
    if not os.path.isdir(d):
        return []
    return sorted((os.path.join(d, f) for f in os.listdir(d) if f.endswith(ext)), reverse=True)


def build():
    os.makedirs(SITE, exist_ok=True)
    digests = read_dir("digest")
    decisions = read_dir("decisions")
    alerts = read_dir("alerts")

    latest_body = ""
    if digests:
        raw = open(digests[0], encoding="utf-8").read()
        raw = re.sub(r"^---\n.*?\n---\n", "", raw, flags=re.S)
        latest_body = md2html(raw)
        os.makedirs(os.path.join(SITE, "digest"), exist_ok=True)
        for path in digests:
            name = os.path.splitext(os.path.basename(path))[0]
            text = re.sub(r"^---\n.*?\n---\n", "", open(path, encoding="utf-8").read(), flags=re.S)
            open(os.path.join(SITE, "digest", name + ".html"), "w", encoding="utf-8").write(
                page("情报简报 " + name, md2html(text), "每日全球情报摘要"))

    archive = "".join(
        f'<li><a href="/digest/{os.path.splitext(os.path.basename(p))[0]}.html">'
        f'{os.path.splitext(os.path.basename(p))[0]}</a></li>' for p in digests[:30])
    index_body = f"""{latest_body}
<h2>历史简报</h2><ul>{archive or '<li>暂无</li>'}</ul>"""

    open(os.path.join(SITE, "index.html"), "w", encoding="utf-8").write(
        page("全球情报简报", index_body, "每日自动采集 · 只基于抓取材料"))

    cards = []
    os.makedirs(os.path.join(SITE, "decisions"), exist_ok=True)
    for path in decisions:
        name = os.path.splitext(os.path.basename(path))[0]
        text = re.sub(r"^---\n.*?\n---\n", "", open(path, encoding="utf-8").read(), flags=re.S)
        first = next((l.strip("# ").strip() for l in text.split("\n") if l.startswith("# ")), name)
        cards.append(f'<div class="card"><a href="/decisions/{name}.html">{html.escape(first)}</a>'
                     f'<div class="meta">{name}</div></div>')
        open(os.path.join(SITE, "decisions", name + ".html"), "w", encoding="utf-8").write(
            page(first, md2html(text), "决策备忘录 · " + name))
    open(os.path.join(SITE, "decisions.html"), "w", encoding="utf-8").write(
        page("决策备忘录", "".join(cards) or "<p>暂无</p>", "结构化决策分析"))

    alert_html = []
    for path in alerts[:14]:
        name = os.path.splitext(os.path.basename(path))[0]
        text = open(path, encoding="utf-8").read()
        alert_html.append(f"<h2>{name}</h2>" + md2html(text))
    open(os.path.join(SITE, "alerts.html"), "w", encoding="utf-8").write(
        page("触发告警", "".join(alert_html) or "<p>暂无触发</p>", "决策备忘录里的触发条件命中记录"))

    # 清掉源文件已删除但页面还留着的陈旧 html（否则站点会积累幽灵页面）
    keep_digest = {os.path.splitext(os.path.basename(p))[0] + ".html" for p in digests}
    keep_dec = {os.path.splitext(os.path.basename(p))[0] + ".html" for p in decisions}
    for sub, keep in (("digest", keep_digest), ("decisions", keep_dec)):
        d = os.path.join(SITE, sub)
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if f.endswith(".html") and f not in keep:
                os.remove(os.path.join(d, f))

    return {"digests": len(digests), "decisions": len(decisions), "alerts": len(alerts)}


if __name__ == "__main__":
    print(build())
