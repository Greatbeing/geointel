#!/usr/bin/env python3
"""review_draft.py — 到期复盘草稿：收集材料 + LLM 建议，人确认后才计分。

原则沿用 review.py：LLM 不直接登记结果（那等于自我打分）。本脚本只做
准备工作，把人工复盘从“自己去翻新闻”变成“看一页草稿、确认或改一个词”：

1. 找出复核日已到（或 --ahead N 天内将到）且未登记结果的备忘录；
2. 用备忘录的问题、情景名、触发器关键词从本地库检索复核期间的材料；
3. 让 LLM 判断哪个情景最符合实际走向，必须引用材料编号，可答 none / unclear；
4. 写 out/reviews/<memo>.md（建议、理由、引用材料、确认命令）；
5. --send 时把待确认清单推飞书。

确认：python3 review.py --memo out/decisions/<memo>.md --occurred "<情景名>|none"
LLM 不可用时仍写草稿（只有材料，没有建议），流程不中断。
"""
import argparse
import json
import os
import re
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import review  # noqa: E402
import score as scoring  # noqa: E402
from memo import load_memo  # noqa: E402

REVIEWS = os.path.join(HERE, "out", "reviews")
WEBHOOK_ENVS = ("GEOINTEL_HEALTH_WEBHOOK", "GEOINTEL_ALERT_WEBHOOK")
MAX_ITEMS = 40

SYSTEM = ("你是严谨的复盘审核员。只根据给出的材料判断，不用自己的背景知识补事实。"
          "材料不足以判断时回答 unclear。只输出 JSON。")


def candidates(rows, today, ahead=0):
    """复核日 <= today+ahead 且未登记结果的行。"""
    limit = today + timedelta(days=ahead)
    out = []
    for r in rows:
        rd = scoring._parse_date(r.get("review_date"))
        if rd and rd <= limit and not r.get("outcome"):
            out.append(r)
    out.sort(key=lambda r: (r.get("review_date") or "", r.get("memo") or ""))
    return out


def memo_path(stem, decisions_dir=None):
    return os.path.join(decisions_dir or review.DECISIONS, stem + ".md")


def query_terms(m):
    data = m["data"]
    terms = []
    for t in data.get("triggers") or []:
        terms += [k for k in (t.get("keywords") or []) if isinstance(k, str)]
    q = data.get("question") or ""
    if q:
        terms.append(q)
    seen, out = set(), []
    for t in terms:
        k = t.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(t.strip())
    return out[:12]


def gather(m, today, search=None, max_items=MAX_ITEMS):
    """检索复核期间（memo 日期起到 today）的材料，按 id 去重。"""
    if search is None:
        import store
        search = store.search
    start = scoring._parse_date(m.get("memo_date")) or scoring._parse_date(m["stem"][:10])
    days = max(7, (today - start).days + 1) if start else 120
    items, seen = [], set()
    for term in query_terms(m):
        try:
            hits = search(term, limit=15, days=days)
        except Exception:
            continue
        for it in hits or []:
            key = it.get("id") or it.get("link") or it.get("title")
            if key and key not in seen:
                seen.add(key)
                items.append(it)
    items.sort(key=lambda it: it.get("published") or "", reverse=True)
    return items[:max_items]


def build_prompt(m, items):
    data = m["data"]
    scen = "\n".join(f"- {s.get('name')}（当时概率 {s.get('prob')}%）"
                     for s in data.get("scenarios") or [])
    mats = "\n".join(
        f"[{i + 1}] {(it.get('published') or '')[:10]} {it.get('source') or ''}｜"
        f"{it.get('title') or ''}｜{(it.get('summary') or '')[:300]}"
        for i, it in enumerate(items)) or "（无材料）"
    return (f"决策问题：{data.get('question') or m['stem']}\n"
            f"复核日：{data.get('review_date')}\n\n情景：\n{scen}\n\n材料：\n{mats}\n\n"
            "判断截至复核日实际走向最符合哪个情景。输出 JSON："
            '{"occurred": "<情景名原文>|none|unclear", "confidence": "high|medium|low", '
            '"reason": "<不超过 150 字>", "evidence": [材料编号,...]}。'
            "none 表示实际走向不在任何情景里；证据不足必须答 unclear。")


def parse_verdict(text, names):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        v = json.loads(m.group(0))
    except ValueError:
        return None
    occ = (v.get("occurred") or "").strip()
    if occ not in names and occ not in ("none", "unclear"):
        # 容忍 LLM 只写了情景名前缀
        pref = [n for n in names if n.startswith(occ) or occ.startswith(n[:12])] if occ else []
        occ = pref[0] if len(pref) == 1 else "unclear"
    ev = [e for e in (v.get("evidence") or []) if isinstance(e, int)]
    if occ not in ("unclear",) and not ev:
        occ = "unclear"  # 没有引用材料的结论不采纳
    return {"occurred": occ, "confidence": v.get("confidence") or "low",
            "reason": (v.get("reason") or "")[:300], "evidence": ev}


def draft(m, items, chat=None):
    names = [s.get("name") for s in m["data"].get("scenarios") or []]
    if not items:
        return {"occurred": "unclear", "confidence": "low",
                "reason": "复核期间库里没有检索到相关材料", "evidence": []}, None
    if chat is None:
        import llm
        chat = llm.chat
    try:
        text = chat(build_prompt(m, items), system=SYSTEM, timeout=120, max_tokens=1500)
    except Exception as e:
        return None, f"LLM 不可用：{e}"
    v = parse_verdict(text, names)
    if v is None:
        return None, "LLM 输出无法解析"
    return v, None


def render_draft(m, row, items, verdict, error):
    stem = m["stem"]
    lines = [f"# 复盘草稿：{m['data'].get('question') or stem}", "",
             f"- 备忘录：`out/decisions/{stem}.md`",
             f"- 复核日：{row.get('review_date')}", ""]
    lines.append("## 情景（当时判断）")
    for s in m["data"].get("scenarios") or []:
        lines.append(f"- {s.get('name')}：{s.get('prob')}%")
    lines += ["", "## 建议（待人工确认，未计分）"]
    if verdict:
        lines += [f"- 建议结果：**{verdict['occurred']}**（置信度 {verdict['confidence']}）",
                  f"- 理由：{verdict['reason']}",
                  f"- 引用材料：{', '.join(f'[{e}]' for e in verdict['evidence']) or '无'}"]
    else:
        lines.append(f"- 无建议：{error}")
    occ = verdict["occurred"] if verdict and verdict["occurred"] != "unclear" else "<情景名或 none>"
    lines += ["", "## 确认命令", "```",
              f'python3 review.py --memo out/decisions/{stem}.md --occurred "{occ}"', "```",
              "", "## 材料"]
    for i, it in enumerate(items):
        lines.append(f"{i + 1}. {(it.get('published') or '')[:10]} {it.get('source') or ''}"
                     f"｜[{it.get('title') or ''}]({it.get('link') or ''})")
    if not items:
        lines.append("（无）")
    return "\n".join(lines) + "\n"


def run(today=None, ahead=0, out_dir=None, scores_path=None, decisions_dir=None,
        search=None, chat=None, force=False):
    today = today or date.today()
    out_dir = out_dir or REVIEWS
    rows = review.load_rows(scores_path or review.SCORES_PATH)
    results = []
    for row in candidates(rows, today, ahead):
        stem = row["memo"]
        path = os.path.join(out_dir, stem + ".md")
        if os.path.exists(path) and not force:
            results.append({"memo": stem, "path": path, "skipped": True})
            continue
        mp = memo_path(stem, decisions_dir)
        if not os.path.exists(mp):
            continue
        m = load_memo(mp)
        m["data"].setdefault("question", row.get("question"))
        m["data"].setdefault("review_date", row.get("review_date"))
        items = gather(m, today, search=search)
        verdict, error = draft(m, items, chat=chat)
        os.makedirs(out_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(render_draft(m, row, items, verdict, error))
        results.append({"memo": stem, "path": path, "verdict": verdict,
                        "error": error, "review_date": row.get("review_date")})
    return results


def format_message(results):
    fresh = [r for r in results if not r.get("skipped")]
    if not fresh:
        return ""
    lines = ["【geointel 复盘待确认】"]
    for r in fresh:
        v = r.get("verdict")
        tip = f"建议「{v['occurred']}」（{v['confidence']}）" if v else (r.get("error") or "无建议")
        lines.append(f"- {r['memo']}（复核日 {r.get('review_date')}）：{tip}")
    lines.append("草稿在 out/reviews/，确认后用 review.py --memo … --occurred … 登记。")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="到期复盘草稿")
    ap.add_argument("--ahead", type=int, default=0, help="提前几天准备草稿，默认 0")
    ap.add_argument("--force", action="store_true", help="已有草稿也重写")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--webhook", default="")
    args = ap.parse_args(argv)
    results = run(ahead=args.ahead, force=args.force)
    for r in results:
        print("review_draft:", r["memo"], "skipped" if r.get("skipped") else
              ((r.get("verdict") or {}).get("occurred") or r.get("error")))
    if not results:
        print("review_draft: 没有到期待复盘的备忘录")
    text = format_message(results)
    if text and args.send:
        url = args.webhook or next((os.environ[e] for e in WEBHOOK_ENVS if os.environ.get(e)), "")
        if url:
            import weekly_summary
            try:
                weekly_summary.send_webhook(url, text)
            except Exception as e:
                print(f"review_draft: 推送失败：{e}", file=sys.stderr)
                return 1
        else:
            print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
