#!/usr/bin/env python3
"""watch.py — 触发条件监控。

读取 out/decisions/*.json 里的 triggers：
- `type=count`（默认）：关键词命中条数达到 threshold → 写告警
- `type=value`：数值类条件（如「核心CPI环比≥0.30%」）需要读指标本身，
  关键词计数无法判定，只输出「需人工读数」提示

用法：watch.py [--hours 24] [--out-dir out/alerts]
"""
import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

import render
import store

HERE = os.path.dirname(os.path.abspath(__file__))


def load_triggers(decisions_dir):
    """读取备忘录里的 triggers。

    同一 slug 只取最新一份：周度重跑会给同一个问题生成新日期的新备忘录，
    旧文件仍留在盘上；全量读取会让同一个触发条件在告警里重复出现。
    """
    latest = {}
    for path in glob.glob(os.path.join(decisions_dir, "*.json")):
        name = os.path.basename(path)[:-5]
        m = re.match(r"^(\d{4}-\d\d-\d\d)-(.+)$", name)
        day, slug = (m.group(1), m.group(2)) if m else ("", name)
        prev = latest.get(slug)
        if prev is None or day >= prev[0]:
            latest[slug] = (day, name, path)

    out = []
    for slug in sorted(latest):
        _day, name, path = latest[slug]
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        for t in data.get("triggers") or []:
            kws = [k.lower() for k in (t.get("keywords") or []) if k]
            if not kws:
                continue
            out.append({
                "decision": name,
                "question": data.get("question", ""),
                "id": t.get("id", "?"),
                "description": t.get("description", ""),
                "action": t.get("action", ""),
                "threshold": t.get("threshold"),
                "kind": (t.get("type") or "count").lower(),
                "keywords": kws,
            })
    return out


def hit(hay, kw):
    """ASCII 关键词要求词边界，否则 'BIS' 会命中 'Guinea-Bissau'、
    'core' 会命中 'CoreWeave'；中日韩关键词没有词边界概念，用子串匹配。"""
    if all(ord(c) < 128 for c in kw):
        return re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", hay) is not None
    return kw in hay


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
    fired = manual = 0
    for t in triggers:
        if t["kind"] == "value":
            manual += 1
            lines.append(f"## [{t['id']}] {t['description']}（需人工读数）")
            lines.append(f"- 关联决策：{t['question']}（{t['decision']}）")
            lines.append(f"- 阈值：{t['threshold']}；线索词：{', '.join(t['keywords'][:6])}")
            if t["action"]:
                lines.append(f"- 触发后动作：{t['action']}")
            lines.append("")
            continue

        hits = []
        for it in items:
            hay = ((it.get("title") or "") + " " + (it.get("summary") or "")).lower()
            if any(hit(hay, k) for k in t["keywords"]):
                hits.append(it)
        try:
            need = float(t["threshold"])
        except (TypeError, ValueError):
            need = 1
        if need <= 0:
            need = 1
        if len(hits) >= need:
            fired += 1
            lines.append(f"## [{t['id']}] {t['description']}")
            lines.append(f"- 关联决策：{t['question']}（{t['decision']}）")
            lines.append(f"- 命中 {len(hits)} 条（阈值 {need:g}）")
            if t["action"]:
                lines.append(f"- 触发后动作：{t['action']}")
            for it in hits[:5]:
                lines.append(f"  - {it['published'][:10]} {it['title']}（{it['source']}）{it['link']}")
            lines.append("")

    if not fired and not manual:
        print(f"{len(triggers)} triggers, none fired")
        return 0

    os.makedirs(args.out_dir, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = os.path.join(args.out_dir, day + ".md")
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"fired {fired}/{len(triggers)} (+{manual} manual-reading) -> {path}")
    render.build()
    return 0


if __name__ == "__main__":
    sys.exit(main())
