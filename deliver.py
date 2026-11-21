#!/usr/bin/env python3
"""deliver.py — 把最新的 geointel 决策 memo 变成一条可直接发聊天的消息。

只读：不改流水线、不调 LLM、不写数据库。只推决策 memo，不推 digest。
用法：
    python3 deliver.py                                              # 最新 memo；今天新出的才输出摘要
    python3 deliver.py --memo out/decisions/2026-11-21-chips-export-control.md
    python3 deliver.py --date 2026-11-21                            # 假装今天是某天（测新鲜度用）
"""
import argparse
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memo as memo_mod  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
DECISIONS_DIR = os.path.join(ROOT, "out", "decisions")
CST = timezone(timedelta(hours=8))  # 北京时间
CONFIDENCE_ZH = {"low": "低", "medium": "中", "high": "高"}


def today_cst():
    return datetime.now(CST).date()


def _first_line_matching(body, pattern):
    for line in body.splitlines():
        if re.search(pattern, line):
            return line
    return None


def extract_recommendation(body):
    """取正文里“推荐：...”那一行（第 6 节建议），去掉 markdown 标记。"""
    line = _first_line_matching(body, r"推荐")
    if not line:
        return None
    text = re.sub(r"[*#]", "", line).strip()
    text = re.sub(r"^推荐[：:]\s*", "", text)
    return text or None


def extract_core(body):
    """取第 1 节“决策核心：...”那一行，截到 120 字。"""
    line = _first_line_matching(body, r"决策核心")
    if not line:
        return None
    text = re.sub(r"[*#]", "", line).strip()
    text = re.sub(r"^.*决策核心[：:]\s*", "", text)
    return text or None


def extract_latest_decision_point(body):
    """取“最晚决策点”后的日期；没有返回 None。"""
    line = _first_line_matching(body, r"最晚决策点")
    if not line:
        return None
    m = re.search(r"(\d{4}-\d{2}-\d{2})", line)
    return m.group(1) if m else None


def _trigger_line(t):
    desc = (t.get("description") or "").strip()
    thr = t.get("threshold")
    action = (t.get("action") or "").strip()
    tid = (t.get("id") or "").upper()
    line = ""
    if tid:
        line += tid + " "
    line += desc
    if thr is not None:
        line += f"（阈值 {thr}）"
    if action:
        line += f" → {action}"
    return line


def format_memo(m):
    """把一个 memo 格式化成聊天可读的摘要（只留关键信息，不全文转储）。"""
    d = m["data"]
    fm = m["frontmatter"]
    question = d.get("question") or fm.get("question", "（未知问题）")
    horizon = d.get("horizon_days") or fm.get("horizon_days", "?")
    items = d.get("items_used") or fm.get("items_used", "?")
    review = d.get("review_date", "?")
    confidence = CONFIDENCE_ZH.get(str(d.get("confidence", "")).lower(), d.get("confidence", "?"))
    lines = [
        f"📰 geointel 决策 memo · {m['memo_date']}",
        "",
        f"**{question}**",
        f"期限 {horizon} 天 · 依据 {items} 条情报 · 复核 {review}",
        "",
    ]
    core = extract_core(m["body"])
    if core:
        lines.append(f"决策核心：{core[:120]}")
        lines.append("")
    scenarios = d.get("scenarios") or []
    if scenarios:
        lines.append("情景：")
        for s in scenarios:
            lines.append(f"- {s.get('name', '?')} {s.get('prob', '?')}%")
        lines.append("")
    triggers = d.get("triggers") or []
    if triggers:
        lines.append("触发条件：")
        for t in triggers:
            lines.append(f"- {_trigger_line(t)}")
        lines.append("")
    rec = extract_recommendation(m["body"])
    if rec:
        lines.append(f"建议：{rec}")
    ldp = extract_latest_decision_point(m["body"])
    tail = f"置信度：{confidence}"
    if ldp:
        tail += f" · 最晚决策点：{ldp}"
    lines.append(tail)
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="输出最新决策 memo 的聊天版摘要")
    ap.add_argument("--memo", help="指定 memo 文件（默认取最新）")
    ap.add_argument("--date", help="假装今天是 YYYY-MM-DD（测新鲜度用）")
    args = ap.parse_args(argv)
    path = args.memo or memo_mod.latest_memo(DECISIONS_DIR)
    if not path:
        print("geointel 今日暂无新决策 memo（out/decisions 下没有 memo）。")
        return 0
    m = memo_mod.load_memo(path)
    today = date.fromisoformat(args.date) if args.date else today_cst()
    if m["memo_date"] != today.isoformat():
        age = (today - date.fromisoformat(m["memo_date"])).days if m["memo_date"] else "?"
        q = m["data"].get("question") or m["frontmatter"].get("question", "")
        print(f"geointel 今日暂无新决策 memo。最新：{m['memo_date']}《{q}》（{age} 天前）。")
        return 0
    print(format_memo(m))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
