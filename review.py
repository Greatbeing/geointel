#!/usr/bin/env python3
"""review.py — geointel Week 4：自动复盘与概率校准。

接 Phase 0 的打分（score.py + scores.jsonl）：
- 结构分沿用 score.py；结果分在复核日期到了时登记实际走向后算
  Brier 分 / 校准度（公式见 docs/scoring-rubric.md）。
- 本脚本不判断“实际发生了什么”——那需要人读新闻做判断，LLM
  猜一个“发生了”就是自我打分。到期未复盘的 memo 列出来等人
  确认；确认后用 --memo / --occurred 登记。
- docs/calibration.html 由 --build-page 从 scores.jsonl 生成，
  用 render.py 的 page() 保持站点风格一致。

用法：
  python3 review.py                                   # 到期/将到期/校准概况
  python3 review.py --due                             # 只列到期未复盘
  python3 review.py --memo out/decisions/2026-11-21-xxx.md --occurred "情景名"
  python3 review.py --memo ... --occurred none        # 实际走向不在情景里
  python3 review.py --build-page                      # 生成 docs/calibration.html
"""
import argparse
import html
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import score as scoring  # noqa: E402
from memo import load_memo  # noqa: E402

DECISIONS = os.path.join(HERE, "out", "decisions")
SCORES_PATH = os.path.join(HERE, "scores.jsonl")
SITE = os.path.join(HERE, "docs")
CALIBRATION_PAGE = os.path.join(SITE, "calibration.html")


def load_rows(path=SCORES_PATH):
    return scoring._read_scores(path)


def due_reviews(rows, today):
    """复核日已到、结果分还是空的 memo（按复核日排序）。"""
    out = []
    for r in rows:
        rd = scoring._parse_date(r.get("review_date"))
        if rd and rd <= today and not r.get("outcome"):
            out.append(r)
    out.sort(key=lambda r: (r.get("review_date") or "", r.get("memo") or ""))
    return out


def upcoming_reviews(rows, today, days=14):
    """未来 days 天内到复核日的 memo（结果分还是空的）。"""
    out = []
    for r in rows:
        rd = scoring._parse_date(r.get("review_date"))
        if rd and today < rd <= _plus_days(today, days) and not r.get("outcome"):
            out.append(r)
    out.sort(key=lambda r: (r.get("review_date") or "", r.get("memo") or ""))
    return out


def next_review(rows, today):
    """最近一个尚未复盘的复核日 (date, row)；没有则 None。

    用来判断固定窗口（14/7 天）是否漏掉了唯一的复核安排：
    备忘录的 horizon 多为 90 天，首个复核日常在 3 周之后，落在
    「14 天」窗口之外就任何视图都看不见，复盘闭环等于没有预警。
    """
    pending = []
    for r in rows:
        rd = scoring._parse_date(r.get("review_date"))
        if rd and rd > today and not r.get("outcome"):
            pending.append((rd, r))
    if not pending:
        return None
    pending.sort(key=lambda x: (x[0], x[1].get("memo") or ""))
    return pending[0]


def upcoming_window(rows, today, days=14):
    """返回 (实际使用的窗口天数, 是否因最近的复核日更远而自动放宽)。

    窗口取 days 与「距最近复核日的天数」的较大值，保证默认视图
    至少能看到下一条要复盘的备忘录，不再静默漏报。
    """
    nxt = next_review(rows, today)
    if nxt is None:
        return days, False
    gap = (nxt[0] - today).days
    if gap > days:
        return gap, True
    return days, False


def _plus_days(d, n):
    from datetime import timedelta

    return d + timedelta(days=n)


def calibration_stats(rows):
    """已复盘 memo 的校准概况；没有已复盘的，各项为 None。"""
    reviewed = [r for r in rows if r.get("outcome")]
    scored = [r for r in rows if r.get("structural")]
    struct = [r["structural"]["score"] for r in scored
              if isinstance(r.get("structural"), dict)
              and isinstance(r["structural"].get("score"), (int, float))]
    stats = {
        "n_total": len(rows),
        "n_scored": len(scored),
        "n_reviewed": len(reviewed),
        "mean_structural": round(sum(struct) / len(struct), 1) if struct else None,
        "mean_brier": None,
        "mean_calibration": None,
        "top_hit_rate": None,
        "coverage_failures": 0,
    }
    if reviewed:
        briers = [r["outcome"]["brier"] for r in reviewed
                  if r["outcome"].get("brier") is not None]
        calibs = [r["outcome"]["calibration"] for r in reviewed
                  if r["outcome"].get("calibration") is not None]
        hits = [r for r in reviewed if r["outcome"].get("top_hit")]
        stats["mean_brier"] = round(sum(briers) / len(briers), 4) if briers else None
        stats["mean_calibration"] = round(sum(calibs) / len(calibs), 1) if calibs else None
        stats["top_hit_rate"] = round(100.0 * len(hits) / len(reviewed), 1)
        stats["coverage_failures"] = sum(
            1 for r in reviewed if r["outcome"].get("coverage_failure"))
    return stats


def review_memo(memo_path, occurred, scores_path=SCORES_PATH, today=None):
    """登记一 memo 的实际走向。occurred 为情景名，或 "none"。
    未到复核日也允许登记，但会打印提醒（提前登记的结果分要慎用）。"""
    m = load_memo(memo_path)
    if today is None:
        today = date.today()
    rd = scoring._parse_date(m["data"].get("review_date"))
    if rd and rd > today:
        print(f"提醒：未到复核日（review_date {rd}），提前登记，结果分仅供参考。")
    outcome = scoring.record_outcome(m, occurred, scores_path)
    return m, outcome


def _esc(s):
    return html.escape(str(s or ""))


def build_page(scores_path=SCORES_PATH, out_path=CALIBRATION_PAGE, today=None):
    """从 scores.jsonl 生成 docs/calibration.html。"""
    import render

    if today is None:
        today = date.today()
    rows = load_rows(scores_path)
    stats = calibration_stats(rows)
    due = due_reviews(rows, today)
    upcoming = upcoming_reviews(rows, today, days=14)

    parts = []
    parts.append(
        "<p class=\"lede\">结构分评 memo 写得规不规范，结果分评预测准不准。"
        "结果分只在复核日期到了、登记了实际走向之后才有；没到期的不猜。</p>")
    s = stats
    summary = [
        f"已评分 {s['n_scored']} 份",
        f"平均结构分 {s['mean_structural'] if s['mean_structural'] is not None else '—'}/100",
        f"已复盘 {s['n_reviewed']} 份",
    ]
    if s["n_reviewed"]:
        summary.append(f"平均 Brier {s['mean_brier']}（越低越好）")
        summary.append(f"平均校准度 {s['mean_calibration']}/100")
        summary.append(f"最高概率情景命中率 {s['top_hit_rate']}%")
        summary.append(f"覆盖失败 {s['coverage_failures']} 次")
    else:
        earliest = min(
            (r.get("review_date") for r in rows if r.get("review_date")),
            default=None)
        summary.append(
            "暂无已复盘 memo" + (f"，首个复核日 {earliest}" if earliest else ""))
    parts.append("<p>" + " · ".join(summary) + "</p>")

    # 已复盘明细
    reviewed = [r for r in rows if r.get("outcome")]
    reviewed.sort(key=lambda r: (r.get("review_date") or "", r.get("memo") or ""))
    parts.append("<h2>已复盘</h2>")
    if not reviewed:
        parts.append("<p>暂无。复核日到了之后，用 review.py --memo … --occurred \"情景名\" 登记。</p>")
    else:
        rows_html = []
        for r in reviewed:
            o = r["outcome"]
            rows_html.append(
                "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                "<td>%s</td><td>%s</td><td>%s</td></tr>" % (
                    _esc(r.get("memo_date")), _esc(r.get("question")),
                    _esc(o.get("occurred")),
                    o.get("brier"), o.get("calibration"),
                    "是" if o.get("top_hit") else "否",
                    "是" if o.get("coverage_failure") else "否"))
        parts.append(
            "<table><thead><tr><th>日期</th><th>问题</th><th>实际走向</th>"
            "<th>Brier</th><th>校准度</th><th>最高概率命中</th>"
            "<th>覆盖失败</th></tr></thead><tbody>"
            + "".join(rows_html) + "</tbody></table>")

    # 待复盘
    parts.append("<h2>待复盘</h2>")
    if due:
        items = "".join(
            "<li>%s 《%s》——复核日 %s，已逾期 %s 天</li>" % (
                _esc(r.get("memo_date")), _esc(r.get("question")),
                _esc(r.get("review_date")),
                (today - scoring._parse_date(r["review_date"])).days)
            for r in due)
        parts.append("<p>已到期未复盘：</p><ul>" + items + "</ul>")
    else:
        parts.append("<p>没有已到期未复盘的 memo。</p>")
    if upcoming:
        items = "".join(
            "<li>%s 《%s》——复核日 %s</li>" % (
                _esc(r.get("memo_date")), _esc(r.get("question")),
                _esc(r.get("review_date")))
            for r in upcoming)
        parts.append("<p>未来 14 天内到复核日：</p><ul>" + items + "</ul>")
    else:
        # 14 天窗口内没有安排也要给出最近一条，避免页面上看不出何时复盘
        nxt = next_review(rows, today)
        if nxt is not None:
            rd, r = nxt
            parts.append(
                "<p>未来 14 天内无复核；最近一次在 %s 天后（%s）：</p><ul><li>%s 《%s》"
"——复核日 %s</li></ul>" % (
                    (rd - today).days, _esc(rd), _esc(r.get("memo_date")),
                    _esc(r.get("question")), _esc(r.get("review_date"))))

    # 全部结构分一览
    parts.append("<h2>结构分一览</h2>")
    all_rows = sorted(rows, key=lambda r: (r.get("memo_date") or "",
                                            r.get("memo") or ""), reverse=True)
    items = "".join(
        "<li>%s 《%s》——结构分 %s/100%s</li>" % (
            _esc(r.get("memo_date")), _esc(r.get("question")),
            (r.get("structural") or {}).get("score", "—"),
            (" · 已复盘，校准度 %s" % r["outcome"]["calibration"])
            if r.get("outcome") and r["outcome"].get("calibration") is not None else "")
        for r in all_rows)
    parts.append("<ul>" + (items or "<li>暂无</li>") + "</ul>")

    content = render.page("概率校准", "复盘 · Brier 分 · 命中率",
                          "".join(parts), current="calibration", big=False)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    return out_path


def _print_status(rows, today):
    stats = calibration_stats(rows)
    print(f"memo 总数：{stats['n_total']}，已评结构分：{stats['n_scored']}，"
          f"已复盘：{stats['n_reviewed']}")
    if stats["mean_structural"] is not None:
        print(f"平均结构分：{stats['mean_structural']}/100")
    if stats["n_reviewed"]:
        print(f"平均 Brier：{stats['mean_brier']}，平均校准度："
              f"{stats['mean_calibration']}/100，"
              f"最高概率情景命中率：{stats['top_hit_rate']}%，"
              f"覆盖失败：{stats['coverage_failures']} 次")
    else:
        print("暂无已复盘 memo，结果分待首个复核日。")
    due = due_reviews(rows, today)
    if due:
        print(f"\n已到期未复盘（{len(due)}）：")
        for r in due:
            print(f"  {r.get('memo_date')} 《{r.get('question')}》"
                  f"——复核日 {r.get('review_date')}")
    else:
        print("\n没有已到期未复盘的 memo。")
    up = upcoming_reviews(rows, today, days=14)
    if up:
        print(f"\n未来 14 天内到复核日（{len(up)}）：")
        for r in up:
            print(f"  {r.get('memo_date')} 《{r.get('question')}》"
                  f"——复核日 {r.get('review_date')}")
    else:
        # 固定 14 天窗口为空时，至少把最近一条复核安排报出来，不留盲区
        win, widened = upcoming_window(rows, today, days=14)
        nxt = next_review(rows, today)
        if nxt is not None:
            rd, r = nxt
            note = f"；窗口已放宽到 {win} 天" if widened else ""
            print(f"\n未来 14 天内无复核{note}；最近一次在 {(rd - today).days} 天后"
                  f"（{rd}）：")
            print(f"  {r.get('memo_date')} 《{r.get('question')}》——复核日 {rd}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="geointel 自动复盘与概率校准")
    ap.add_argument("--memo", help="要登记实际走向的 memo 文件（.md）")
    ap.add_argument("--occurred", help="实际发生的情景名，或 none（实际走向不在情景里）")
    ap.add_argument("--due", action="store_true", help="只列到期未复盘")
    ap.add_argument("--build-page", action="store_true",
                    help="生成 docs/calibration.html")
    ap.add_argument("--date", help="按指定日期（YYYY-MM-DD）算到期，默认今天")
    ap.add_argument("--scores", default=SCORES_PATH, help="scores.jsonl 路径")
    args = ap.parse_args(argv)

    today = scoring._parse_date(args.date) if args.date else date.today()
    if today is None:
        print("日期格式不对，用 YYYY-MM-DD。", file=sys.stderr)
        return 2

    if args.memo:
        if not args.occurred:
            print("登记实际走向需要 --occurred \"情景名\"（或 none）。", file=sys.stderr)
            return 2
        m, outcome = review_memo(args.memo, args.occurred, args.scores, today)
        print(f"memo: {m['stem']}")
        print(f"实际走向：{outcome['occurred']}")
        print(f"Brier：{outcome['brier']}，校准度：{outcome['calibration']}/100，"
              f"最高概率命中：{'是' if outcome['top_hit'] else '否'}，"
              f"覆盖失败：{'是' if outcome['coverage_failure'] else '否'}")
        return 0

    rows = load_rows(args.scores)
    if args.due:
        due = due_reviews(rows, today)
        if not due:
            print("没有已到期未复盘的 memo。")
        for r in due:
            print(f"{r.get('memo_date')} 《{r.get('question')}》"
                  f"——复核日 {r.get('review_date')}")
        return 0

    if args.build_page:
        out = build_page(args.scores, today=today)
        print(f"calibration 页已生成：{out}")
        return 0

    _print_status(rows, today)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
