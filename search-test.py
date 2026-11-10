#!/usr/bin/env python3
"""Test store.search with Chinese vs English queries."""
import sys

sys.path.insert(0, "/data/workspace/geointel")
import store

queries = [
    "未来 90 天美联储加息的概率与应对",
    "Federal Reserve rate hike CPI inflation FOMC",
    "美联储 CPI 加息",
]
for q in queries:
    items = store.search(q, limit=5, days=30)
    print(f"query: {q!r} -> {len(items)} items")
    for it in items[:3]:
        print("   ", it["source"], "|", it["title"][:70])
    print()
