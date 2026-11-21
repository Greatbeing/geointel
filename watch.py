#!/usr/bin/env python3
"""watch.py — 触发条件监控。

读取 out/decisions/*.json 里的 triggers：
- `type=count`（默认）：关键词命中条数达到 threshold → 写告警
- `type=value` + `series`（Week 2）：按 market_series 的数值判定，
  最近 window 条观测全部满足 op 与 threshold 的关系 → 写告警；
  无 series 字段或数据缺失/过期时，只输出「需人工读数」提示

触发器可带的可选字段（decide.py 模板 Week 3 会写）：
  series: market_series 里的序列名，如 "DFF" / "USDCNY"
  op: ">="（默认）/ ">" / "<=" / "<" / "=="
  window: 最近几条观测都要满足（默认 1，如“连续两月”可填 2）

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
# 数值触发用的市场数据超过这么多天没更新，就判为过期，不拿旧数判定
VALUE_STALE_DAYS = 7


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
            trig = {
                "decision": name,
                "question": data.get("question", ""),
                "id": t.get("id", "?"),
                "description": t.get("description", ""),
                "action": t.get("action", ""),
                "threshold": t.get("threshold"),
                "kind": (t.get("type") or "count").lower(),
                "keywords": kws,
            }
            # value 型才用的数值判定字段（没有也不报错，走人工读数）
            for extra in ("series", "op", "window"):
                if t.get(extra) is not None:
                    trig[extra] = t.get(extra)
            # count 型没有关键词无法判定；value 型有 series 即可数值判定
            if trig["kind"] == "value" and trig.get("series"):
                out.append(trig)
            elif kws:
                out.append(trig)
    return out


def hit(hay, kw):
    """ASCII 关键词要求词边界，否则 'BIS' 会命中 'Guinea-Bissau'、
    'core' 会命中 'CoreWeave'；中日韩关键词没有词边界概念，用子串匹配。"""
    if all(ord(c) < 128 for c in kw):
        return re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", hay) is not None
    return kw in hay


OPS = {
    ">=": lambda v, t: v >= t,
    ">": lambda v, t: v > t,
    "<=": lambda v, t: v <= t,
    "<": lambda v, t: v < t,
    "==": lambda v, t: v == t,
}


def eval_value_trigger(t):
    """按 market_series 判定一个 value 型触发器。
    返回 (status, detail)：status 为 "fired" / "not-fired" / "no-data" / "stale" / "manual"。"""
    series = t.get("series")
    if not series:
        return "manual", "未绑定 market_series，需人工读数"
    try:
        threshold = float(t.get("threshold"))
    except (TypeError, ValueError):
        return "manual", "阈值不是数值，需人工读数"
    op = (t.get("op") or ">=").strip()
    if op not in OPS:
        return "manual", f"未知比较符 {op!r}，需人工读数"
    try:
        window = int(t.get("window") or 1)
    except (TypeError, ValueError):
        window = 1
    if window < 1:
        window = 1
    hist = store.market_history(series, limit=window)
    if not hist:
        return "no-data", f"market_series 里没有 {series} 的数据"
    if len(hist) < window:
        return "no-data", f"{series} 只有 {len(hist)} 条观测，需要 {window} 条"
    newest = hist[0][0]
    try:
        age = (datetime.now(timezone.utc).date()
               - datetime.strptime(newest, "%Y-%m-%d").date()).days
    except ValueError:
        return "no-data", f"{series} 最新日期 {newest} 不是合法日期"
    if age > VALUE_STALE_DAYS:
        return "stale", f"{series} 最新数据是 {newest}（{age} 天前），已过期"
    check = OPS[op]
    if all(check(v, threshold) for _d, v in hist):
        vals = "、".join(f"{d} {v:g}" for d, v in hist)
        return "fired", f"观测值：{vals}（{series} {op} {threshold:g}，连续 {window} 条满足）"
    vals = "、".join(f"{d} {v:g}" for d, v in hist)
    return "not-fired", f"观测值：{vals}（未满足 {series} {op} {threshold:g}）"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--decisions-dir", default=os.path.join(HERE, "out", "decisions"))
    ap.add_argument("--out-dir", default=os.path.join(HERE, "out", "alerts"))
    args = ap.parse_args(argv)

    triggers = load_triggers(args.decisions_dir)
    if not triggers:
        print("no triggers defined")
        return 0

    items = store.recent(hours=args.hours, limit=800)
    lines = [f"统计窗口：过去 {args.hours} 小时，{len(items)} 条材料。", ""]
    notes = []  # 未触发 / 数据不足的 value 触发器：只打日志，不进告警文件
    fired = manual = 0
    for t in triggers:
        if t["kind"] == "value":
            status, detail = eval_value_trigger(t)
            if status == "fired":
                fired += 1
                lines.append(f"## [{t['id']}] {t['description']}")
                lines.append(f"- 关联决策：{t['question']}（{t['decision']}）")
                lines.append(f"- {detail}")
                if t["action"]:
                    lines.append(f"- 触发后动作：{t['action']}")
                lines.append("")
            elif status == "manual":
                manual += 1
                lines.append(f"## [{t['id']}] {t['description']}（需人工读数）")
                lines.append(f"- 关联决策：{t['question']}（{t['decision']}）")
                lines.append(f"- 阈值：{t['threshold']}；{detail}")
                if t["action"]:
                    lines.append(f"- 触发后动作：{t['action']}")
                lines.append("")
            else:
                notes.append(f"value [{t['id']}] {status}：{detail}")
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
        for n in notes:
            print(f"  {n}")
        return 0

    os.makedirs(args.out_dir, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = os.path.join(args.out_dir, day + ".md")
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"fired {fired}/{len(triggers)} (+{manual} manual-reading) -> {path}")
    for n in notes:
        print(f"  {n}")
    render.build()
    return 0


if __name__ == "__main__":
    sys.exit(main())
