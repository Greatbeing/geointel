#!/usr/bin/env python3
"""watch.py — 触发条件监控。

读取 out/decisions/*.json 里的 triggers（关键词 + 阈值），
在过去 N 小时的条目里统计命中数，达到阈值就写一条告警。

用法：watch.py [--hours 24] [--out-dir out/alerts]
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

import render
import store

HERE = os.path.dirname(os.path.abspath(__file__))


def load_triggers(decisions_dir):
    out = []
    for path in sorted(glob.glob(os.path.join(decisions_dir, "*.json"))):
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        for t in data.get("triggers") or []:
            kws = [k.lower() for k in (t.get("keywords") or []) if k]
            if not kws:
                continue
            out.append({
                "decision": os.path.basename(path)[:-5],
                "question": data.get("question", ""),
                "id": t.get("id", "?"),
                "description": t.get("description", ""),
                "action": t.get("action", ""),
                "threshold": int(t.get("threshold") or 1),
                "keywords": kws,
            })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--decisions-dir", default=os.path.join(HERE, "out", "decisions"))
    ap.add_argument("--out-dir", default=os.path.join(HERE, "out", "alerts"))
    args = ap.parse_args()

    triggers = load_triggers(args.decisions_dir)
    if not triggers:
        print("no triggers defined")
        return 0

    items = store.recent(hours=args.hours, limit=800)
    lines = [f"统计窗口：过去 {args.hours} 小时，{len(items)} 条材料。", ""]
    fired = 0
    for t in triggers:
        hits = []
        for it in items:
            hay = ((it.get("title") or "") + " " + (it.get("summary") or "")).lower()
            if any(k in hay for k in t["keywords"]):
                hits.append(it)
        if len(hits) >= t["threshold"]:
            fired += 1
            lines.append(f"## [{t['id']}] {t['description']}")
            lines.append(f"- 关联决策：{t['question']}（{t['decision']}）")
            lines.append(f"- 命中 {len(hits)} 条（阈值 {t['threshold']}）")
            if t["action"]:
                lines.append(f"- 触发后动作：{t['action']}")
            for it in hits[:5]:
                lines.append(f"  - {it['published'][:10]} {it['title']}（{it['source']}）{it['link']}")
            lines.append("")

    if not fired:
        print(f"{len(triggers)} triggers, none fired")
        return 0

    os.makedirs(args.out_dir, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = os.path.join(args.out_dir, day + ".md")
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"fired {fired}/{len(triggers)} -> {path}")
    render.build()
    return 0


if __name__ == "__main__":
    sys.exit(main())
