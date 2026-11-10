#!/usr/bin/env python3
"""collect.py — 并发抓取 RSS/Atom/RDF，解析、去重、入库。

用法：
  collect.py [--sources sources.json] [--workers 8] [--timeout 20]

设计：
- 只用标准库（沙箱没有 feedparser，也不装）
- 每条按规范化 URL 的 sha1 去重；同一条被多家转载只留第一次见到的
- 单个源失败不影响整体；每次运行写一份 data/runs/<时间>.json 记录成功/失败
"""
import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import store

UA = "Mozilla/5.0 (compatible; geointel/1.0; +https://github.com/Greatbeing)"
HERE = os.path.dirname(os.path.abspath(__file__))
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
TRACKING = ("utm_", "fbclid", "gclid", "ref=", "cmpid", "ocid")


def clean(text, limit=700):
    if not text:
        return ""
    text = html.unescape(TAG_RE.sub(" ", text))
    text = WS_RE.sub(" ", text).strip()
    return text[:limit]


def local(tag):
    return tag.split("}")[-1].lower()


def parse_date(value):
    if not value:
        return None
    value = value.strip()
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def canonical(url):
    if not url:
        return ""
    url = url.strip()
    parts = urllib.parse.urlsplit(url)
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query)
             if not any(k.lower().startswith(t) or k.lower() == t for t in TRACKING)]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc.lower(),
                                    parts.path.rstrip("/"), urllib.parse.urlencode(query), ""))


ENTITY_RE = re.compile(rb"&([A-Za-z][A-Za-z0-9]{1,31});")
XML_BUILTIN = {b"amp", b"lt", b"gt", b"quot", b"apos"}


def sanitize(data):
    """把 XML 未定义但 HTML 常见的实体（&nbsp; &mdash; 等）换成字符。

    标准库 ElementTree 遇到未定义实体会直接 ParseError，而现实里的
    RSS 里到处是 HTML 实体。先整体替换成数字实体，再交给 ET。
    """
    def repl(m):
        name = m.group(1)
        if name in XML_BUILTIN:
            return m.group(0)
        ch = html.entities.html5.get(name.decode("ascii") + ";")
        if not ch:
            return b""
        return ("&#%d;" % ord(ch)).encode("ascii")

    return ENTITY_RE.sub(repl, data)


def parse_feed(data, source):
    root = ET.fromstring(sanitize(data))
    items = []
    for el in root.iter():
        if local(el.tag) not in ("item", "entry"):
            continue
        title = link = summary = published = ""
        for child in el:
            name = local(child.tag)
            if name == "title":
                title = clean("".join(child.itertext()), 300)
            elif name == "link":
                link = (child.get("href") or "".join(child.itertext()) or "").strip()
            elif name in ("description", "summary", "content", "encoded"):
                if not summary:
                    summary = clean("".join(child.itertext()))
            elif name in ("pubdate", "published", "updated", "date", "created"):
                if not published:
                    published = parse_date("".join(child.itertext()))
        if not title:
            continue
        link = canonical(link)
        key = hashlib.sha1((link or f"{source['id']}:{title}").encode("utf-8")).hexdigest()
        items.append({
            "id": key,
            "source": source["id"],
            "region": source.get("region", ""),
            "category": source.get("category", ""),
            "title": title,
            "link": link,
            "summary": summary,
            "published": published,
            "weight": source.get("weight", 1.0),
        })
    return items


def fetch(source, timeout):
    req = urllib.request.Request(source["url"], headers={"User-Agent": UA,
                                                         "Accept": "application/rss+xml, application/xml, text/xml, */*"})
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(4_000_000)
        if data[:2] == b"\x1f\x8b":
            import gzip
            data = gzip.decompress(data)
        stripped = data.lstrip()
        if stripped[:15].lower().startswith(b"<!doctype html") or stripped[:6].lower().startswith(b"<html"):
            return source["id"], False, 0, round(time.time() - started, 1), [], \
                "non-XML response (HTML page - dead or wrong feed URL)"
        items = parse_feed(data, source)
        return source["id"], True, len(items), round(time.time() - started, 1), items, ""
    except Exception as e:
        return source["id"], False, 0, round(time.time() - started, 1), [], f"{type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default=os.path.join(HERE, "sources.json"))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--timeout", type=int, default=20)
    args = ap.parse_args()

    sources = json.load(open(args.sources, encoding="utf-8"))["sources"]
    print(f"collect: {len(sources)} sources", flush=True)

    all_items, report = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch, s, args.timeout): s for s in sources}
        for fut in as_completed(futures):
            sid, ok, n, secs, items, err = fut.result()
            report.append({"source": sid, "ok": ok, "items": n, "seconds": secs, "error": err})
            if ok:
                all_items.extend(items)
            print(f"  {'OK  ' if ok else 'FAIL'} {sid:<22} {n:>3} items {secs:>5}s {err[:80]}", flush=True)

    # 同一次运行里也去重（同一 URL 可能出现在多个聚合源）
    seen, unique = set(), []
    for it in all_items:
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        unique.append(it)

    new = store.upsert_many(unique)
    report.sort(key=lambda r: (not r["ok"], r["source"]))
    summary = {
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": len(sources),
        "ok": sum(1 for r in report if r["ok"]),
        "failed": [r["source"] for r in report if not r["ok"]],
        "fetched": len(unique),
        "new": new,
        "per_source": report,
    }
    runs = os.path.join(HERE, "data", "runs")
    os.makedirs(runs, exist_ok=True)
    path = os.path.join(runs, summary["at"].replace(":", "") + ".json")
    json.dump(summary, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"done: {summary['ok']}/{summary['sources']} sources ok, "
          f"{len(unique)} fetched, {new} new -> {path}", flush=True)
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
