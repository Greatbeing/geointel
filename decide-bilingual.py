#!/usr/bin/env python3
"""Fix decide.py retrieval: Chinese questions must also match English intelligence.
Problem (2026-11-11): decide searches with a Chinese question, but items are in
English; FTS finds no match and falls back to the most recent (irrelevant) items,
so the memo honestly reports 'material insufficient' even when CPI/Fed data IS
in the DB. Fix: expand the question with English keywords for recognized Chinese
terms before calling store.search (FTS does OR over the tokens).
"""
import os

ROOT = "/data/workspace/geointel"
p = os.path.join(ROOT, "decide.py")
text = open(p, encoding="utf-8").read()

# 1. Add the bilingual keyword map + expand_query() before the TEMPLATE definition.
anchor = "# Decision model: NVIDIA Nemotron 3 Ultra 550B via Magpie (user 2026-11-11:"
assert anchor in text, "DECIDE_MODEL anchor missing"
expansion = '''# Bilingual retrieval (user 2026-11-11 "continue fixing"): intelligence items are
# mostly English, but decision questions are Chinese. FTS on Chinese terms alone
# finds nothing and store.search falls back to recent (irrelevant) items. Expand
# recognised Chinese topic words with English keywords so the OR match hits the
# English content. Add new topics here as they arise.
CN_EN_KEYWORDS = {
    "美联储": "Federal Reserve Fed FOMC",
    "联储": "Federal Reserve Fed FOMC",
    "加息": "rate hike interest rate",
    "升息": "rate hike interest rate",
    "降息": "rate cut interest rate",
    "通胀": "inflation CPI PCE",
    "通货膨胀": "inflation CPI PCE",
    "CPI": "CPI inflation",
    "PCE": "PCE inflation",
    "非农": "nonfarm payroll employment jobs",
    "就业": "employment jobs unemployment",
    "失业率": "unemployment jobless",
    "利率": "interest rate",
    "央行": "central bank",
    "美元": "dollar USD",
    "半导体": "semiconductor chip",
    "芯片": "semiconductor chip",
    "出口管制": "export control",
    "出口": "export",
    "贸易": "trade tariff",
    "关税": "tariff",
    "原油": "oil crude WTI",
    "油价": "oil crude price",
    "能源": "energy oil gas",
    "地缘": "geopolitical",
    "人工智能": "AI artificial intelligence",
    "AI": "AI artificial intelligence",
    "科技": "technology tech",
    "股市": "stock market equity",
    "债市": "bond Treasury yield",
    "国债": "Treasury bond yield",
    "黄金": "gold",
    "气候": "climate",
    "选举": "election",
}


def expand_query(question):
    """Append English keywords for any recognised Chinese topic words."""
    extra = []
    for cn, en in CN_EN_KEYWORDS.items():
        if cn in question:
            extra.append(en)
    if extra:
        return question + " " + " ".join(extra)
    return question


'''
text = text.replace(anchor, expansion + anchor, 1)

# 2. Use the expanded query in the store.search call.
old_search = "items = store.search(args.question, limit=args.items, days=args.days)"
new_search = "items = store.search(expand_query(args.question), limit=args.items, days=args.days)"
assert old_search in text, "store.search call anchor missing"
text = text.replace(old_search, new_search, 1)

open(p, "w", encoding="utf-8").write(text)
print("decide.py patched: bilingual query expansion (CN_EN_KEYWORDS + expand_query)")
