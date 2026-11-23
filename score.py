#!/usr/bin/env python3
"""score.py — 给 geointel 决策 memo 打分（确定性检查，不调 LLM）。

两层，标准见 docs/scoring-rubric.md：
  (a) 结构分（出 memo 当天就能评，满分 100）
  (b) 结果分（复核日期到了才评）——Brier 校准分 + 最高概率情景是否命中

用法：
  python3 score.py <memo.md>                                  # 结构分，并记入 scores.jsonl
  python3 score.py --all                                      # 给 out/decisions 下所有 memo 评结构分
  python3 score.py --outcome <memo.md> --occurred "加速收紧"   # 复核日：登记实际发生的情景
  python3 score.py --outcome <memo.md> --occurred none        # 实际走向不在列出的情景里
"""
import argparse
import json
import os
import re
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memo as memo_mod  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
DECISIONS_DIR = os.path.join(ROOT, "out", "decisions")
SCORES_PATH = os.path.join(ROOT, "scores.jsonl")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _as_number(x):
    """数值或数值字符串 -> 数字；否则 None（front-matter 的值是字符串）。"""
    if _is_number(x):
        return x
    try:
        return int(str(x).strip())
    except (TypeError, ValueError):
        try:
            return float(str(x).strip())
        except (TypeError, ValueError):
            return None


def _parse_date(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def structural_score(m):
    """结构分 0-100。返回 (总分, 扣分原因列表 [(扣分, 原因)])。"""
    d = m["data"]
    fm = m["frontmatter"]
    body = m["body"]
    deductions = []
    total = 0

    # 1. 概率完整性（20 分）
    scenarios = d.get("scenarios") or []
    s1 = 20
    if len(scenarios) < 2:
        deductions.append((10, f"情景只有 {len(scenarios)} 个，少于 2 个"))
        s1 -= 10
    prob_sum = 0
    bad = 0
    for sc in scenarios:
        if not sc.get("name") or not _is_number(sc.get("prob")):
            bad += 1
        else:
            prob_sum += sc["prob"]
    if bad:
        pts = min(10, 5 * bad)
        deductions.append((pts, f"{bad} 个情景缺名字或概率"))
        s1 -= pts
    if scenarios and abs(prob_sum - 100) > 1:
        deductions.append((10, f"情景概率和为 {prob_sum}，不是 100"))
        s1 -= 10
    total += max(0, s1)

    # 2. 触发条件可证伪（40 分，每条触发器满分 10 分）
    triggers = d.get("triggers") or []
    if not triggers:
        deductions.append((40, "没有触发条件"))
    else:
        t_sum = 0
        for t in triggers:
            tid = (t.get("id") or "?").upper()
            pts = 0
            desc = (t.get("description") or "").strip()
            if len(desc) >= 10:
                pts += 3
            else:
                deductions.append((3, f"{tid} 触发条件描述太短（<10 字）或缺失"))
            if _is_number(t.get("threshold")):
                pts += 3
            else:
                deductions.append((3, f"{tid} 触发条件没有数值阈值"))
            if (t.get("action") or "").strip():
                pts += 2
            else:
                deductions.append((2, f"{tid} 触发条件没有对应动作"))
            ttype = (t.get("type") or "").lower()
            kws = t.get("keywords") or []
            groups = t.get("keyword_groups") or []
            if ttype == "count":
                if kws or groups:
                    pts += 2
                else:
                    deductions.append((2, f"{tid} 为 count 型但没有关键词"))
            elif ttype == "value":
                if re.search(r"\d|%", desc):
                    pts += 2
                else:
                    deductions.append((2, f"{tid} 为 value 型但描述里没有数字或百分号（数值口径不明）"))
            else:
                if kws:
                    pts += 2
                else:
                    deductions.append((2, f"{tid} 类型不明且没有关键词"))
            t_sum += pts
        if len(triggers) < 4:
            # 触发器越少上限越低：这部分失分要记下来，不能静默丢分
            deductions.append(
                (40 - 10 * len(triggers), f"触发器只有 {len(triggers)} 条，少于 4 条（每少一条上限少 10 分）")
            )
        total += min(40, t_sum)

    # 3. 置信度（10 分）
    if str(d.get("confidence", "")).lower() in ("low", "medium", "high"):
        total += 10
    else:
        deductions.append((10, f"置信度缺失或不在 low/medium/high 内：{d.get('confidence')}"))

    # 4. 复核日期（15 分）
    review = _parse_date(d.get("review_date"))
    gen = _parse_date(d.get("generated_at") or fm.get("generated_at"))
    horizon = _as_number(d.get("horizon_days") if d.get("horizon_days") is not None else fm.get("horizon_days"))
    if review is None:
        deductions.append((15, "复核日期缺失或不是合法日期"))
    else:
        total += 5
        if gen is not None and review > gen:
            total += 5
        else:
            deductions.append((5, "复核日期不晚于生成日期"))
        if gen is not None and _is_number(horizon):
            delta = (review - gen).days
            if abs(delta - horizon) <= 3:
                total += 5
            else:
                deductions.append((5, f"复核日期与生成日期相差 {delta} 天，与期限 {horizon} 天不一致"))
        else:
            deductions.append((5, "无法核对复核日期与期限是否一致（缺生成日期或期限）"))

    # 5. 最晚决策点（10 分）
    if "最晚决策点" in body:
        if DATE_RE.search(body.split("最晚决策点", 1)[1][:80]):
            total += 10
        else:
            total += 5
            deductions.append((5, "写了最晚决策点但没给日期"))
    else:
        deductions.append((10, "正文没有最晚决策点"))

    # 6. 证据基础（5 分）
    items = _as_number(d.get("items_used") if d.get("items_used") is not None else fm.get("items_used"))
    if _is_number(items) and items >= 10:
        total += 5
    elif _is_number(items) and items >= 1:
        total += 2
        deductions.append((3, f"依据情报只有 {items} 条，少于 10 条"))
    else:
        deductions.append((5, "没有记录依据情报条数（items_used）"))

    return min(100, total), deductions


def brier_score(probs, occurred_idx):
    """Brier 分数。probs 为 0-1 的概率列表；occurred_idx 为实际发生的情景下标，
    None 表示实际走向不在列出的情景里。返回 (brier, calibration 0-100 分)。
    Brier 越低越好；calibration = 100 * (1 - brier/2)，越高越好。"""
    n = len(probs)
    if n == 0:
        return None, None
    total = 0.0
    for i, p in enumerate(probs):
        o = 1.0 if (occurred_idx is not None and i == occurred_idx) else 0.0
        total += (p - o) ** 2
    brier = total / n
    calibration = max(0.0, 100.0 * (1.0 - brier / 2.0))
    return brier, calibration


def _read_scores(path):
    rows = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def _write_scores(rows, path):
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def record_structural(m, score, deductions, path=SCORES_PATH):
    """结构分记入 scores.jsonl（按 memo 去重更新，保留已有的结果分）。"""
    rows = _read_scores(path)
    now = datetime.now().isoformat(timespec="seconds")
    # slug：去掉文件名日期前缀，供 Week 4 的 review.py 直接用
    # （例：2026-11-21-chips-export-control -> chips-export-control）
    slug = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", m["stem"])
    entry = {
        "memo": m["stem"],
        "slug": slug,
        "memo_date": m["memo_date"],
        "question": m["data"].get("question") or m["frontmatter"].get("question"),
        "generated_at": m["data"].get("generated_at") or m["frontmatter"].get("generated_at"),
        "review_date": m["data"].get("review_date"),
        "structural": {
            "score": score,
            "deductions": [{"points": p, "reason": r} for p, r in deductions],
        },
        "outcome": None,
        "scored_at": now,
    }
    for i, row in enumerate(rows):
        if row.get("memo") == m["stem"]:
            entry["outcome"] = row.get("outcome")
            rows[i] = entry
            break
    else:
        rows.append(entry)
    _write_scores(rows, path)
    return entry


def record_outcome(m, occurred, path=SCORES_PATH):
    """复核日登记实际走向。occurred 为情景名，或 "none"（实际走向不在情景里）。
    返回 outcome 记录；情景名不存在时抛 ValueError。"""
    scenarios = m["data"].get("scenarios") or []
    names = [s.get("name") for s in scenarios]
    probs = [float(s.get("prob", 0)) / 100.0 for s in scenarios]
    if occurred == "none":
        idx, top_hit, coverage_failure = None, False, True
    else:
        if occurred not in names:
            raise ValueError(f"情景名 {occurred!r} 不在 memo 的情景里：{names}")
        idx = names.index(occurred)
        top_hit = idx == max(range(len(probs)), key=lambda i: probs[i])
        coverage_failure = False
    brier, calib = brier_score(probs, idx)
    rows = _read_scores(path)
    now = datetime.now().isoformat(timespec="seconds")
    outcome = {
        "occurred": occurred,
        "brier": round(brier, 4) if brier is not None else None,
        "calibration": round(calib, 1) if calib is not None else None,
        "top_hit": top_hit,
        "coverage_failure": coverage_failure,
        "scored_at": now,
    }
    for i, row in enumerate(rows):
        if row.get("memo") == m["stem"]:
            row["outcome"] = outcome
            rows[i] = row
            break
    else:
        score, deductions = structural_score(m)
        entry = record_structural(m, score, deductions, path)
        entry["outcome"] = outcome
        rows = _read_scores(path)
        for i, row in enumerate(rows):
            if row.get("memo") == m["stem"]:
                rows[i] = entry
                break
    _write_scores(rows, path)
    return outcome


def _print_structural(m, score, deductions):
    print(f"memo: {m['stem']}")
    print(f"结构分：{score}/100")
    if not deductions:
        print("扣分：无")
    else:
        print("扣分：")
        for pts, reason in deductions:
            print(f"  -{pts} 分：{reason}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="给 geointel 决策 memo 打分")
    ap.add_argument("memo", nargs="?", help="memo 文件（.md）")
    ap.add_argument("--all", action="store_true", help="给 out/decisions 下所有 memo 评结构分")
    ap.add_argument("--outcome", help="复核日用：memo 文件，登记实际走向")
    ap.add_argument("--occurred", help='实际发生的情景名，或 "none"（实际走向不在情景里）')
    args = ap.parse_args(argv)

    if args.all:
        paths = sorted(
            os.path.join(DECISIONS_DIR, n)
            for n in os.listdir(DECISIONS_DIR)
            if n.endswith(".md")
        )
        for p in paths:
            try:
                m = memo_mod.load_memo(p)
                score, deductions = structural_score(m)
                record_structural(m, score, deductions)
                print(f"{m['stem']}: {score}/100" + ("" if not deductions else f"（扣 {len(deductions)} 项）"))
            except Exception as e:  # noqa: BLE001 — 单个 memo 失败不挡住整批
                print(f"{os.path.basename(p)}: 评分失败：{e}")
        return 0

    target = args.outcome or args.memo
    if not target:
        ap.error("请指定一个 memo 文件，或用 --all 批量评分")
    m = memo_mod.load_memo(target)
    if args.outcome:
        if not args.occurred:
            ap.error("--outcome 需要同时给 --occurred")
        try:
            outcome = record_outcome(m, args.occurred)
        except ValueError as e:
            print(f"错误：{e}")
            return 1
        print(f"memo: {m['stem']}")
        print(f"实际走向：{args.occurred}")
        print(f"Brier：{outcome['brier']}（越低越好，范围 0-2）")
        print(f"校准分：{outcome['calibration']}/100")
        print(f"最高概率情景命中：{'是' if outcome['top_hit'] else '否'}")
        if outcome["coverage_failure"]:
            print("注意：实际走向不在列出的情景里——情景覆盖失效，见 rubric。")
        return 0
    score, deductions = structural_score(m)
    record_structural(m, score, deductions)
    _print_structural(m, score, deductions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
