#!/usr/bin/env python3
"""geointel source optimization round 2 — run ON being in /data/workspace/geointel.
- Remove arxiv-ai (arXiv RSS consistently returns an empty feed, 0 items).
- Add gn-trade (global trade — trade category had only WTO).
- Add gn-latam (Latin America — no dedicated LatAm source).
- Add gn-climate (climate policy — only energy-markets coverage before).
All three new sources are Google News RSS, verified 100 items each.
"""
import json
import os

ROOT = "/data/workspace/geointel"
sj = os.path.join(ROOT, "sources.json")
data = json.load(open(sj, encoding="utf-8"))

before = len(data["sources"])
data["sources"] = [s for s in data["sources"] if s["id"] != "arxiv-ai"]
removed = before - len(data["sources"])

new_sources = [
    {
        "id": "gn-trade",
        "name": "Google News — Global Trade",
        "url": "https://news.google.com/rss/search?q=global+trade&hl=en-US&gl=US&ceid=US:en",
        "region": "global",
        "category": "trade",
        "weight": 0.9,
    },
    {
        "id": "gn-latam",
        "name": "Google News — Latin America",
        "url": "https://news.google.com/rss/search?q=latin+america&hl=en-US&gl=US&ceid=US:en",
        "region": "americas",
        "category": "world",
        "weight": 0.8,
    },
    {
        "id": "gn-climate",
        "name": "Google News — Climate Policy",
        "url": "https://news.google.com/rss/search?q=climate+policy&hl=en-US&gl=US&ceid=US:en",
        "region": "global",
        "category": "world",
        "weight": 0.8,
    },
]
ids = {s["id"] for s in data["sources"]}
for s in new_sources:
    if s["id"] not in ids:
        data["sources"].append(s)
        ids.add(s["id"])

json.dump(data, open(sj, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"removed arxiv-ai: {removed}; added: {[s['id'] for s in new_sources]}; total sources: {len(data['sources'])}")
