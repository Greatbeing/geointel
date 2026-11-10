#!/usr/bin/env python3
"""digest.py — 每日情报简报。

流程：取过去 N 小时条目 → 去重 → 按权重/时效排序并保证分类覆盖 →
把材料交给 magpie（group/daily，LongCat 首选）→ 生成中文简报 → 写盘 → 重建站点。

用法：
  digest.py [--hours 24] [--max-items 70] [--out-dir out/digest]
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

import llm
import render
import store

HERE = os.path.dirname(os.path.abspath(__file__))

SYSTEM = (
    "你是一名严谨的情报分析师。你只根据给定材料写作，绝不补充材料之外的事实。"
    "如果材料不足以支撑某个判断，就明确写「材料不足」。不写套话，不写空泛的趋势判断。"
)

TEMPLATE = """下面是过去 {hours} 小时抓取到的 {n} 条材料（编号、来源、时间、标题、摘要、链接）。
请用中文写一份每日情报简报，严格按以下结构：

## 今日要点
5 条，每条一句话，句末用（来源）标注。

## 区域动态
按 亚太 / 欧洲 / 中东 / 非洲 / 美洲 分组，有材料的组写 2–4 条；没有材料的组写「材料不足」。

## 技术与产业
3–6 条，聚焦半导体、AI、能源、供应链等有实际进展的事件。

## 金融与政策
3–6 条，聚焦央行、监管、贸易政策、资本流动。

## 值得跟踪的信号
3 条。每条写：现象 → 为什么值得跟踪 → 下一步看什么可观测指标（具体到能查的数据或事件）。

硬规则：
- 只使用材料里出现的事实、数字、机构名；不要推断材料没有的因果。
- 每条尽量带日期；不确定就写「据报道」。
- 不要输出材料编号列表，不要复述任务要求。

材料：
{material}
"""


def dedupe(items):
    seen, out = set(), []
    for it in items:
        key = re.sub(r"[^a-z0-9]+", "", (it["title"] or "").lower())[:70]
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def pick(items, max_items):
    """按 权重 × 时效 排序，同时保证每个分类至少若干条。"""
    now = datetime.now(timezone.utc)

    def score(it):
        try:
            age_h = (now - datetime.strptime(it["published"], "%Y-%m-%dT%H:%M:%SZ")
                     .replace(tzinfo=timezone.utc)).total_seconds() / 3600
        except Exception:
            age_h = 24
        return float(it.get("weight") or 1.0) * (1.0 + max(0.0, 24 - age_h) / 24)

    ranked = sorted(items, key=score, reverse=True)
    chosen, per_cat = [], {}
    for it in ranked:
        cat = it.get("category") or "other"
        if len(chosen) < max_items and per_cat.get(cat, 0) < 14:
            chosen.append(it)
            per_cat[cat] = per_cat.get(cat, 0) + 1
    for it in ranked:
        if len(chosen) >= max_items:
            break
        if it not in chosen:
            chosen.append(it)
    return chosen[:max_items]


def material(items):
    lines = []
    for i, it in enumerate(items, 1):
        summary = (it.get("summary") or "")[:220].replace("\n", " ")
        lines.append(f"[{i}] ({it['source']} · {it['published'][:16].replace('T', ' ')}Z) "
                     f"{it['title']} — {summary} | {it['link']}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--max-items", type=int, default=70)
    ap.add_argument("--out-dir", default=os.path.join(HERE, "out", "digest"))
    args = ap.parse_args()

    raw = store.recent(hours=args.hours, limit=600)
    if not raw:
        print("no items in window; run collect first", file=sys.stderr)
        return 2
    items = pick(dedupe(raw), args.max_items)

    stats = store.stats()
    print(f"digest: {len(raw)} raw -> {len(items)} selected "
          f"(db total {stats['total']}, last24h {stats['last_24h']})", flush=True)

    prompt = TEMPLATE.format(hours=args.hours, n=len(items), material=material(items))
    body = llm.chat(prompt, system=SYSTEM, timeout=420, max_tokens=16000)

    now = datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    os.makedirs(args.out_dir, exist_ok=True)
    path = os.path.join(args.out_dir, f"{day}.md")
    header = (
        "---\n"
        f"date: {day}\n"
        f"generated_at: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        f"items_selected: {len(items)}\n"
        f"items_in_window: {len(raw)}\n"
        f"db_total: {stats['total']}\n"
        "---\n\n"
    )
    open(path, "w", encoding="utf-8").write(header + body.strip() + "\n")

    meta = {
        "date": day,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": [{"title": it["title"], "source": it["source"], "link": it["link"],
                   "published": it["published"], "category": it.get("category")}
                  for it in items],
    }
    json.dump(meta, open(os.path.join(args.out_dir, f"{day}.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"written: {path}", flush=True)
    render.build()
    return 0


if __name__ == "__main__":
    sys.exit(main())
