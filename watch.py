#!/usr/bin/env python3
"""watch.py — 触发条件监控与实时告警。

读取 out/decisions/*.json 里的 triggers：
- `type=count`（默认）：关键词命中条数达到 threshold → 写告警。
  旧 `keywords` 保持“任一词命中”的兼容语义；Week 5 的
  `keyword_groups` 为“组内任一、组间全部”，`exclude_keywords` 任一命中即排除。
- `type=value` + `series`（Week 2）：按 market_series 的数值判定，
  最近 window 条观测全部满足 op 与 threshold 的关系 → 写告警；
  无 series 字段或数据缺失/过期时，只输出「需人工读数」提示。

触发器可带的可选字段：
  series: market_series 里的序列名，如 "DFF" / "USDCNY"
  op: ">="（默认）/ ">" / "<=" / "<" / "=="
  window: 最近几条观测都要满足（默认 1，如“连续两月”可填 2）
  keyword_groups: [["盟友", "ally"], ["export control", "export restriction"]]
  exclude_keywords: ["education", "exam"]

用法：
  watch.py [--hours 24] [--out-dir out/alerts]
  GEOINTEL_ALERT_WEBHOOK=https://... watch.py --send

站点报告保留当前所有活跃告警；--send 只通知“上次未活跃、这次活跃”的
触发器，成功后才写状态，避免每 2 小时重复推送，网络失败也不会丢通知。
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
import weekly_summary

HERE = os.path.dirname(os.path.abspath(__file__))
# 数值触发用的市场数据超过这么多天没更新，就判为过期，不拿旧数判定
VALUE_STALE_DAYS = 7
ALERT_WEBHOOK_ENV = "GEOINTEL_ALERT_WEBHOOK"


def _terms(value):
    """只接受字符串数组；坏数据降级为空，不让一个触发器拖垮整轮。"""
    if not isinstance(value, (list, tuple)):
        return []
    return [k.strip().lower() for k in value if isinstance(k, str) and k.strip()]


def _groups(value):
    if not isinstance(value, (list, tuple)):
        return []
    return [g for g in (_terms(group) for group in value) if g]


def load_triggers(decisions_dir):
    """读取备忘录里的 triggers；同一 slug 只取最新一份。"""
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
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        for t in data.get("triggers") or []:
            if not isinstance(t, dict):
                continue
            kws = _terms(t.get("keywords"))
            groups = _groups(t.get("keyword_groups"))
            trig = {
                "decision": name,
                "question": data.get("question", ""),
                "id": t.get("id", "?"),
                "description": t.get("description", ""),
                "action": t.get("action", ""),
                "threshold": t.get("threshold"),
                "kind": (t.get("type") or "count").lower(),
                "keywords": kws,
                "keyword_groups": groups,
                "exclude_keywords": _terms(t.get("exclude_keywords")),
            }
            # value 型才用的数值判定字段（没有也不报错，走人工读数）
            for extra in ("series", "op", "window"):
                if t.get(extra) is not None:
                    trig[extra] = t.get(extra)
            if trig["kind"] == "value":
                # 没有 series 也必须进入人工读数清单，不能静默丢弃。
                out.append(trig)
            elif kws or groups:
                out.append(trig)
    return out


def hit(hay, kw):
    """ASCII 关键词要求词边界，否则 'BIS' 会命中 'Guinea-Bissau'、
    'core' 会命中 'CoreWeave'；中日韩关键词没有词边界概念，用子串匹配。
    ASCII 词尾允许可选复数 s（export control → export controls），不放大写。"""
    if all(ord(c) < 128 for c in kw):
        tail = r"s?" if not kw.endswith("s") else ""
        return re.search(r"(?<![a-z0-9])" + re.escape(kw) + tail + r"(?![a-z0-9])",
                         hay) is not None
    return kw in hay


def count_match(t, hay):
    """一条材料是否满足 count 触发器。

    新 schema 的组间 AND 是“同一篇材料内”约束，不是把不同文章的关键词
    拼起来；这样才不会由一篇日本社会新闻加一篇出口管制新闻凑出误报。
    """
    if any(hit(hay, k) for k in t.get("exclude_keywords") or []):
        return False
    groups = t.get("keyword_groups") or []
    if groups:
        return all(any(hit(hay, k) for k in group) for group in groups)
    return any(hit(hay, k) for k in t.get("keywords") or [])


def count_hits(t, items):
    hits = []
    for it in items:
        hay = ((it.get("title") or "") + " " + (it.get("summary") or "")).lower()
        if count_match(t, hay):
            hits.append(it)
    return hits


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
    vals = "、".join(f"{d} {v:g}" for d, v in hist)
    if all(check(v, threshold) for _d, v in hist):
        return "fired", f"观测值：{vals}（{series} {op} {threshold:g}，连续 {window} 条满足）"
    return "not-fired", f"观测值：{vals}（未满足 {series} {op} {threshold:g}）"


def _event(t, text):
    return {"key": f"{t['decision']}#{t['id']}", "text": text.strip()}


def load_notification_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        active = data.get("active")
        if not isinstance(active, list):
            raise ValueError("active is not a list")
        return {"active": [str(k) for k in active]}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {"active": []}


def save_notification_state(path, active):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    data = {
        "active": sorted(set(active)),
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def send_new_alerts(events, state_path, webhook):
    """只推送新进入活跃状态的告警；发送成功后才推进状态。

    活跃告警从当前轮消失后，状态也随之清除；以后再次触发可以重新通知。
    返回本次实际推送包含的新告警数。
    """
    previous = set(load_notification_state(state_path)["active"])
    current = {e["key"] for e in events}
    fresh = [e for e in events if e["key"] not in previous]
    if fresh:
        text = "geointel 新触发告警\n\n" + "\n\n".join(e["text"] for e in fresh)
        weekly_summary.send_webhook(webhook, text)
    save_notification_state(state_path, current)
    return len(fresh)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--decisions-dir", default=os.path.join(HERE, "out", "decisions"))
    ap.add_argument("--out-dir", default=os.path.join(HERE, "out", "alerts"))
    ap.add_argument("--send", action="store_true", help="把新触发的告警推送到飞书")
    ap.add_argument("--webhook", default="", help=f"飞书 Webhook；也可用 {ALERT_WEBHOOK_ENV}")
    ap.add_argument("--state", default=os.path.join(HERE, "data", "alert-state.json"),
                    help="实时通知去重状态（默认在 gitignore 的 data/ 下）")
    args = ap.parse_args(argv)

    triggers = load_triggers(args.decisions_dir)
    if not triggers:
        print("no triggers defined")
        return 0

    items = store.recent(hours=args.hours, limit=800)
    lines = [f"统计窗口：过去 {args.hours} 小时，{len(items)} 条材料。", ""]
    notes = []  # 未触发 / 数据不足的 value 触发器：只打日志，不进告警文件
    fired = manual = 0
    fired_events = []
    for t in triggers:
        if t["kind"] == "value":
            status, detail = eval_value_trigger(t)
            if status == "fired":
                fired += 1
                start = len(lines)
                lines.append(f"## [{t['id']}] {t['description']}")
                lines.append(f"- 关联决策：{t['question']}（{t['decision']}）")
                lines.append(f"- {detail}")
                if t["action"]:
                    lines.append(f"- 触发后动作：{t['action']}")
                lines.append("")
                fired_events.append(_event(t, "\n".join(lines[start:])))
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

        hits = count_hits(t, items)
        try:
            need = float(t["threshold"])
        except (TypeError, ValueError):
            need = 1
        if need <= 0:
            need = 1
        if len(hits) >= need:
            fired += 1
            start = len(lines)
            lines.append(f"## [{t['id']}] {t['description']}")
            lines.append(f"- 关联决策：{t['question']}（{t['decision']}）")
            lines.append(f"- 命中 {len(hits)} 条（阈值 {need:g}）")
            if t["action"]:
                lines.append(f"- 触发后动作：{t['action']}")
            for it in hits[:5]:
                lines.append(f"  - {it['published'][:10]} {it['title']}（{it['source']}）{it['link']}")
            lines.append("")
            fired_events.append(_event(t, "\n".join(lines[start:])))

    wrote = bool(fired or manual)
    path = None
    if wrote:
        os.makedirs(args.out_dir, exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = os.path.join(args.out_dir, day + ".md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"fired {fired}/{len(triggers)} (+{manual} manual-reading) -> {path}")
    else:
        print(f"{len(triggers)} triggers, none fired")
    for n in notes:
        print(f"  {n}")

    rc = 0
    if args.send:
        webhook = args.webhook or os.environ.get(ALERT_WEBHOOK_ENV)
        if not webhook and fired_events:
            print(f"未配置实时告警渠道：请用 --webhook 或设置 {ALERT_WEBHOOK_ENV}。", file=sys.stderr)
            rc = 2
        elif webhook:
            try:
                fresh = send_new_alerts(fired_events, args.state, webhook)
                print(f"实时通知：{fresh} 条新告警，{len(fired_events) - fresh} 条持续告警已去重")
            except Exception as e:  # noqa: BLE001 — 状态未推进，下轮会重试
                print(f"实时通知失败：{e}", file=sys.stderr)
                rc = 1
    if wrote:
        render.build()
    return rc


if __name__ == "__main__":
    sys.exit(main())
