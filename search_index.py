"""Build a client-side search index for the static site (std-lib only).

Writes docs/search.json: [{t: title, k: kicker, u: url, d: date, x: text}]
and a docs/search.html page that filters it in the browser. No server,
no third-party JS. Rebuilt by render.py on every run.
"""
import html
import json
import os
import re

STOP = set("的 了 和 与 或 在 是 对 及 其 把 被 从 到 为 以 也 都 而 并 等 中 上 下 个 这 那 有 不 之 所 将 并".split())


def tokens(text):
    out = []
    for w in re.findall(r"[a-z0-9][a-z0-9\-]{1,}|[\u4e00-\u9fff]{2,}", (text or "").lower()):
        if w in STOP:
            continue
        # CJK: also emit bigrams so a query like "出口管制" matches a longer run
        if "\u4e00" <= w[0] <= "\u9fff" and len(w) > 2:
            out += [w[i:i + 2] for i in range(len(w) - 1)]
        out.append(w)
    return set(out)


def strip_md(text):
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\|.*$", " ", text, flags=re.M)
    text = re.sub(r"[#>*_`\-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def build_index(entries):
    """entries: iterable of dict(title, kicker, url, date, text)."""
    out = []
    for e in entries:
        body = strip_md(e.get("text", ""))[:6000]
        out.append({"t": e["title"], "k": e.get("kicker", ""), "u": e["url"],
                    "d": e.get("date", ""), "x": body,
                    "tok": sorted(tokens(e["title"] + " " + body))[:400]})
    return out


PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>检索</title>
<style>__CSS__</style></head><body>
<div class="top"><a class="brand" href="__HOME__">__MARK__</a><nav>__NAV__</nav></div>
<div class="wrap"><header class="masthead"><p class="kicker">全站检索</p>
<h1 class="dateline">检索</h1>
<p class="tagline">简报、备忘录、告警，按词找。</p></header>
<div class="body">
<input id="q" type="search" placeholder="输入关键词，如 出口管制、美债、复盘" autocomplete="off"
 style="width:100%;font:inherit;font-size:18px;padding:12px 14px;margin:14px 0 6px;border:1px solid var(--rule-2);background:var(--paper);color:var(--ink);border-radius:0">
<p id="hint" style="color:var(--ink-3);font-size:13px;margin:0 0 24px"></p>
<ol id="res" class="index-list" style="padding-left:0"></ol>
</div></div>
<script>
const $=id=>document.getElementById(id);
let data=null;
fetch('search.json').then(r=>r.json()).then(d=>{data=d;$('hint').textContent='共 '+d.length+' 条，输入即搜。';run();});
const q=new URLSearchParams(location.search).get('q')||'';$('q').value=q;
function run(){ if(!data)return; const s=$('q').value.trim().toLowerCase();
 const terms=s.split(/\\s+/).filter(Boolean);
 const res=!s?[]:data.map(e=>{let sc=0;for(const t of terms){
   if(e.t.toLowerCase().includes(t))sc+=5; else if(e.x.toLowerCase().includes(t))sc+=2; else if(e.tok.includes(t))sc+=1; else return null;}
   return [sc,e];}).filter(Boolean).sort((a,b)=>b[0]-a[0]||(a[1].d<b[1].d?1:-1)).slice(0,40);
 $('res').innerHTML=res.map(([sc,e])=>{let snip='';
   if(s){const i=e.x.toLowerCase().indexOf(terms[0]);if(i>=0)snip=e.x.slice(Math.max(0,i-40),i+80);}
   return '<li><span class="when">'+e.d+'</span><p class="what"><a href="'+e.u+'">'+esc(e.t)+'</a></p>'+(snip?'<p class="note">'+esc(snip)+'…</p>':'')+'</li>';}).join('');
 history.replaceState(null,'',s?'search.html?q='+encodeURIComponent(s):'search.html');
 $('hint').textContent=s?(res.length?'找到 '+res.length+' 条':'没有匹配'):'共 '+data.length+' 条，输入即搜。';
}
function esc(s){return s.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
let tm;$('q').addEventListener('input',()=>{clearTimeout(tm);tm=setTimeout(run,120);});
</script></body></html>
"""


def write_search(site_dir, entries, css, home, mark, nav):
    import io
    os.makedirs(site_dir, exist_ok=True)
    idx = build_index(entries)
    with open(os.path.join(site_dir, "search.json"), "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, separators=(",", ":"))
    page = (PAGE.replace("__CSS__", css).replace("__HOME__", home)
            .replace("__MARK__", mark).replace("__NAV__", nav))
    with open(os.path.join(site_dir, "search.html"), "w", encoding="utf-8") as f:
        f.write(page)
    return len(idx)
