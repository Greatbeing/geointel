#!/usr/bin/env python3
"""weekly_summary.py — geointel Week 4：每周汇总推送（飞书）。

汇总内容（全部来自仓库已有数据，不调 LLM）：
- 本周新决策 memo（标题、主情景+概率、结构分、复核日）
- 未来 7 天到复核日的 memo、已逾期未复盘的 memo
- 概率校准快照（已复盘数、平均校准度、最高概率命中率）

推送渠道——飞书自定义机器人 Webhook（用户 2026-11-22 定）：
- 默认只打印（--dry-run），不发往任何地方。
- --send 时把汇总按飞书标准格式 POST 到 Webhook URL：
  {"msg_type": "text", "content": {"text": "..."}}。
  URL 来自 --webhook 或环境变量 GEOINTEL_WEEKLY_WEBHOOK。
  没给 URL 就停下并明说缺什么，不猜地址，不把真实 URL 写进代码。
- 飞书机器人若开了“签名校验”，光 POST 会被拒（返回非 0 code，
  本程序会报失败）；到时用户说一声，再加签名逻辑。

用法：
  python3 weekly_summary.py                      # 打印本周汇总
  python3 weekly_summary.py --days 7 --out /tmp/summary.md
  GEOINTEL_WEEKLY_WEBHOOK=https://... python3 weekly_summary.py --send
"""
import argparse
import json
import os
import sys
import urllib.request
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import review as review_mod  # noqa: E402
import score as scoring  # noqa: E402
from memo import load_memo, memo_date_from_name  # noqa: E402

DECISIONS = os.path.join(HERE, "out", "decisions")
SCORES_PATH = os.path.join(HERE, "scores.jsonl")
WEBHOOK_ENV = "GEOINTEL_WEEKLY_WEBHOOK"


def _parse_date(s):
    return scoring._parse_date(s)


def new_memos(decisions_dir, start, end):
    """start < memo_date <= end 的 memo（按日期排序）。"""
    out = []
    if not os.path.isdir(decisions_dir):
        return out
    for name in sorted(os.listdir(decisions_dir)):
        if not name.endswith(".md"):
            continue
        d = _parse_date(memo_date_from_name(name))
        if d and start < d <= end:
            try:
                out.append(load_memo(os.path.join(decisions_dir, name)))
            except Exception:
                continue
    return out


def build_summary(decisions_dir=DECISIONS, scores_path=SCORES_PATH,
                  today=None, days=7):
    """生成每周汇总 markdown。纯数据拼接，不调 LLM。"""
    if today is None:
        today = date.today()
    start = today - timedelta(days=days)
    rows = review_mod.load_rows(scores_path)
    by_memo = {r.get("memo"): r for r in rows}
    stats = review_mod.calibration_stats(rows)
    due = review_mod.due_reviews(rows, today)
    upcoming = review_mod.upcoming_reviews(rows, today, days=7)
    memos = new_memos(decisions_dir, start, today)

    lines = [f"# geointel 每周汇总（{start} ~ {today}）", ""]
    lines.append(f"## 本周新决策（{len(memos)}）")
    if not memos:
        lines.append("本周没有新决策 memo。")
    for m in memos:
        data = m["data"]
        scen = data.get("scenarios") or []
        top = max(scen, key=lambda s: s.get("prob") or 0) if scen else {}
        row = by_memo.get(m["stem"], {})
        struct = (row.get("structural") or {}).get("score")
        lines.append(
            f"- {m['memo_date']}《{data.get('question') or m['stem']}》："
            f"主情景 {top.get('name', '—')} {top.get('prob', '—')}%"
            + (f" · 结构分 {struct}/100" if struct is not None else " · 结构分未评")
            + (f" · 复盘 {data.get('review_date')}" if data.get("review_date") else ""))
    lines.append("")

    lines.append("## 即将复盘")
    if not upcoming:
        nxt = review_mod.next_review(rows, today)
        if nxt is not None:
            rd, r = nxt
            lines.append(f"未来 7 天内无复核；最近一次在 {(rd - today).days} 天后（{rd}）：")
            lines.append(f"- {r.get('memo_date')}《{r.get('question')}》——复核日 {rd}")
        else:
            lines.append("无。")
    for r in upcoming:
        lines.append(f"- {r.get('memo_date')}《{r.get('question')}》"
                     f"——复核日 {r.get('review_date')}")
    lines.append("")

    lines.append("## 已逾期未复盘")
    if not due:
        lines.append("无。")
    for r in due:
        lines.append(f"- {r.get('memo_date')}《{r.get('question')}》"
                     f"——复核日 {r.get('review_date')}")
    lines.append("")

    lines.append("## 概率校准")
    if stats["n_reviewed"]:
        lines.append(
            f"已复盘 {stats['n_reviewed']} 份，平均 Brier {stats['mean_brier']}，"
            f"平均校准度 {stats['mean_calibration']}/100，"
            f"最高概率情景命中率 {stats['top_hit_rate']}%，"
            f"覆盖失败 {stats['coverage_failures']} 次。")
    else:
        earliest = min(
            (r.get("review_date") for r in rows if r.get("review_date")),
            default=None)
        lines.append("暂无已复盘 memo，结果分待首个复核日"
                     + (f"（{earliest}）" if earliest else "。"))
    lines.append(f"结构分：{stats['n_scored']} 份已评，"
                 f"平均 {stats['mean_structural'] if stats['mean_structural'] is not None else '—'}/100。")
    lines.append("")
    return "\n".join(lines)


FEISHU_MAX_TEXT = 10000  # 飞书 text 消息正文上限留余，超长截断
FEISHU_TRUNCATED = "\n……（内容过长，已截断）"


def _to_plain_text(md):
    """汇总是 markdown；飞书 text 消息不渲染 markdown，
    去掉标题行的 # 号，其余原样保留（换行、列表都在）。"""
    out = []
    for line in md.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            line = stripped.lstrip("#").strip()
        out.append(line)
    return "\n".join(out)


def build_feishu_payload(text):
    """把汇总转成飞书自定义机器人 text 消息：
    {"msg_type": "text", "content": {"text": "..."}}。
    纯函数，不发网，便于测试。"""
    plain = _to_plain_text(text).strip("\n")
    if len(plain) > FEISHU_MAX_TEXT:
        plain = plain[:FEISHU_MAX_TEXT - len(FEISHU_TRUNCATED)] + FEISHU_TRUNCATED
    return {"msg_type": "text", "content": {"text": plain}}


def send_webhook(url, text, timeout=15):
    """POST 飞书 text 消息到自定义机器人 Webhook。返回 HTTP 状态码。
    飞书成功时返回 {"code": 0, ...}；code 非 0 说明被拒
    （如签名校验未过、关键词不匹配），按失败处理并抛出原因。"""
    body = json.dumps(build_feishu_payload(text),
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = resp.status
        raw = resp.read() if hasattr(resp, "read") else b""
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            data = {}
        code = data.get("code") if isinstance(data, dict) else None
        if code is not None and code != 0:
            raise RuntimeError(
                f"飞书拒绝推送：code={code}，msg={data.get('msg')}")
        return status


def main(argv=None):
    ap = argparse.ArgumentParser(description="geointel 每周汇总推送")
    ap.add_argument("--days", type=int, default=7, help="汇总过去几天，默认 7")
    ap.add_argument("--date", help="按指定日期（YYYY-MM-DD）算，默认今天")
    ap.add_argument("--out", help="把汇总写到文件（默认只打印）")
    ap.add_argument("--dry-run", action="store_true", default=True,
                    help="只打印，不发送（默认）")
    ap.add_argument("--send", action="store_true", help="发送到 Webhook")
    ap.add_argument("--webhook", default=None,
                    help="Webhook URL；不给则读环境变量 GEOINTEL_WEEKLY_WEBHOOK")
    ap.add_argument("--scores", default=SCORES_PATH, help="scores.jsonl 路径")
    ap.add_argument("--decisions", default=DECISIONS, help="out/decisions 目录")
    args = ap.parse_args(argv)

    today = _parse_date(args.date) if args.date else date.today()
    if today is None:
        print("日期格式不对，用 YYYY-MM-DD。", file=sys.stderr)
        return 2

    text = build_summary(args.decisions, args.scores, today, days=args.days)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)

    if args.send:
        url = args.webhook or os.environ.get(WEBHOOK_ENV)
        if not url:
            print("未配置推送渠道：没有飞书自定义机器人 Webhook URL。",
                  file=sys.stderr)
            print(f"给一个地址就能发：设置环境变量 {WEBHOOK_ENV}=<Webhook URL>，"
                  "或用 --webhook <URL>。", file=sys.stderr)
            print("地址在飞书群里拿：群设置 → 群机器人 → 自定义机器人 → "
                  "复制 Webhook 地址。", file=sys.stderr)
            return 2
        try:
            status = send_webhook(url, text)
        except Exception as e:
            print(f"推送失败：{e}", file=sys.stderr)
            return 1
        print(f"已推送到 Webhook，HTTP {status}。")
        return 0

    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
