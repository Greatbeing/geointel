#!/usr/bin/env python3
"""health.py — 运行健康检查与告警。

两件事：
1. 记录：每个定时任务结束时调用 `health.py record <job> <rc> [--note ...]`，
   写 data/health/<job>.json（时间、退出码、备注）。
2. 检查：`health.py check [--send]`
   - 每个任务：最近一次退出码非 0，或太久没运行（超过 JOB_MAX_AGE_HOURS）→ 问题；
   - 市场数据：每个序列最新日期超过 N 个工作日 → 问题；
   问题集合与上次不同时才推飞书（新增问题 / 已恢复），避免每小时刷屏。

Webhook：--webhook，或环境变量 GEOINTEL_HEALTH_WEBHOOK，没有则回退 GEOINTEL_ALERT_WEBHOOK。
"""
import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
HEALTH_DIR = os.path.join(HERE, "data", "health")
STATE_PATH = os.path.join(HERE, "data", "health-state.json")
WEBHOOK_ENVS = ("GEOINTEL_HEALTH_WEBHOOK", "GEOINTEL_ALERT_WEBHOOK")

# 任务最长允许多久没有成功记录（小时）。没有记录过的任务不报（可能本机没装）。
JOB_MAX_AGE_HOURS = {
    "collect": 8,
    "digest": 30,
    "market": 30,
    "watch": 30,
    "decide": 30,
    "publish": 30,
}
MARKET_MAX_BDAYS = 5
# 个别序列发布天然滞后更久（如 FRED 美元广义指数按周发布）
MARKET_MAX_BDAYS_OVERRIDE = {"DTWEXBGS": 10, "CPILFESL": 45, "PCEPILFE": 45}


def now_utc():
    return datetime.now(timezone.utc)


def record(job, rc, note="", health_dir=None, now=None):
    health_dir = health_dir or HEALTH_DIR
    os.makedirs(health_dir, exist_ok=True)
    now = now or now_utc()
    path = os.path.join(health_dir, f"{job}.json")
    prev = {}
    try:
        with open(path, encoding="utf-8") as f:
            prev = json.load(f)
    except (OSError, ValueError):
        pass
    row = {
        "job": job,
        "rc": int(rc),
        "at": now.isoformat(timespec="seconds"),
        "note": note[-500:],
        "last_ok": now.isoformat(timespec="seconds") if int(rc) == 0 else prev.get("last_ok"),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(row, f, ensure_ascii=False, indent=1)
    return row


def check_jobs(health_dir=None, now=None, max_age=None):
    health_dir = health_dir or HEALTH_DIR
    now = now or now_utc()
    max_age = max_age or JOB_MAX_AGE_HOURS
    problems = {}
    try:
        names = sorted(n for n in os.listdir(health_dir) if n.endswith(".json"))
    except OSError:
        return problems
    for n in names:
        try:
            with open(os.path.join(health_dir, n), encoding="utf-8") as f:
                row = json.load(f)
        except (OSError, ValueError):
            continue
        job = row.get("job") or n[:-5]
        if row.get("rc", 0) != 0:
            note = (row.get("note") or "").strip().splitlines()
            tail = f"：{note[-1][:120]}" if note else ""
            problems[f"job:{job}:fail"] = f"任务 {job} 最近一次失败（退出码 {row.get('rc')}，{row.get('at')}）{tail}"
        limit = max_age.get(job)
        last_ok = row.get("last_ok")
        if limit and last_ok:
            age_h = (now - datetime.fromisoformat(last_ok)).total_seconds() / 3600
            if age_h > limit:
                problems[f"job:{job}:stale"] = f"任务 {job} 已 {age_h:.0f} 小时没有成功运行（上限 {limit} 小时）"
    return problems


def check_market(today=None, latest=None):
    import market_collect as mc
    today = today or date.today()
    if latest is None:
        latest = mc.store.latest_market()
    problems = {}
    for name in mc.SERIES:
        row = (latest or {}).get(name)
        limit = MARKET_MAX_BDAYS_OVERRIDE.get(name, MARKET_MAX_BDAYS)
        if not row or not row.get("date"):
            problems[f"market:{name}"] = f"市场序列 {name} 库里没有数据"
            continue
        age = mc.business_days_between(date.fromisoformat(row["date"]), today)
        if age > limit:
            problems[f"market:{name}"] = f"市场序列 {name} 最新 {row['date']}，已 {age} 个工作日未更新（上限 {limit}）"
    return problems


def diff_state(problems, state_path=None):
    """返回 (新增问题, 已恢复问题)，并把当前问题集合写回状态文件。"""
    state_path = state_path or STATE_PATH
    old = {}
    try:
        with open(state_path, encoding="utf-8") as f:
            old = json.load(f).get("active", {})
    except (OSError, ValueError):
        pass
    new = {k: v for k, v in problems.items() if k not in old}
    fixed = {k: v for k, v in old.items() if k not in problems}
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump({"active": problems, "at": now_utc().isoformat(timespec="seconds")},
                  f, ensure_ascii=False, indent=1)
    return new, fixed


def format_message(new, fixed, active):
    lines = ["【geointel 运行健康】"]
    if new:
        lines.append("新问题：")
        lines += [f"- {v}" for v in new.values()]
    if fixed:
        lines.append("已恢复：")
        lines += [f"- {v}" for v in fixed.values()]
    still = [v for k, v in active.items() if k not in new]
    if still:
        lines.append("仍未解决：")
        lines += [f"- {v}" for v in still]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("job")
    r.add_argument("rc", type=int)
    r.add_argument("--note", default="")
    c = sub.add_parser("check")
    c.add_argument("--send", action="store_true")
    c.add_argument("--webhook", default="")
    c.add_argument("--no-market", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "record":
        record(args.job, args.rc, args.note)
        return 0

    problems = check_jobs()
    if not args.no_market:
        try:
            problems.update(check_market())
        except Exception as e:  # 库读不到本身就是问题
            problems["market:read"] = f"读取市场数据失败：{e}"
    for v in problems.values():
        print("PROBLEM", v)
    if not problems:
        print("health: ok")
    new, fixed = diff_state(problems)
    if not (new or fixed):
        return 0
    text = format_message(new, fixed, problems)
    if not args.send:
        print(text)
        return 0
    webhook = args.webhook or next((os.environ[e] for e in WEBHOOK_ENVS if os.environ.get(e)), "")
    if not webhook:
        print("health: 未配置 Webhook（GEOINTEL_HEALTH_WEBHOOK / GEOINTEL_ALERT_WEBHOOK），只打印不推送", file=sys.stderr)
        print(text)
        return 0
    import weekly_summary
    try:
        weekly_summary.send_webhook(webhook, text)
        print("health: 已推送")
    except Exception as e:
        print(f"health: 推送失败：{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
