#!/usr/bin/env python3
"""decide.py — 决策备忘录生成器。

输入一个问题，从情报库里检索相关材料，让 magpie 产出结构化的决策备忘录：
事实 → 不确定性 → 情景与概率 → 选项评估 → 建议与触发条件 → 证伪条件 → 复盘日期。

用法：
  decide.py "问题" [--days 30] [--slug my-slug] [--items 40]

输出：
  out/decisions/<日期>-<slug>.md    正文（含 JSON 块）
  out/decisions/<日期>-<slug>.json  机读版（scenarios/triggers/confidence）
"""
import argparse
import hashlib
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
    "你是一名决策分析师，风格接近情报机构的评估报告：区分事实与判断，给区间而不是点估计，"
    "明确写出什么证据会推翻结论。你不迎合提问者，也不假装确定。"
)

TEMPLATE = """决策问题：{question}

时间范围：未来 {horizon} 天。

可用情报（过去 {days} 天抓取，共 {n} 条，含来源与日期）：
{material}

请用中文输出一份决策备忘录，严格按以下结构：

# {question}

## 1. 问题与边界
说明这个决策真正要选的是什么、不包含什么、最晚什么时候必须决定。

## 2. 已知事实
5–8 条，每条格式：事实 ——（来源，日期）。只写材料里有的。

## 3. 关键不确定性
3–5 个变量。每个写：变量名、当前区间、判断依据、为什么它重要。

## 4. 情景
3 个互斥情景，给出概率（整数，合计 100）与每个情景下的关键观察点。

## 5. 选项评估
至少 3 个选项（含「不做决定」）。每个写：预期收益、最坏情况、可逆性、执行成本。

## 6. 建议与触发条件
明确推荐一个选项，并给 2–4 个触发条件：可观测指标 + 阈值 + 触发后的动作。
触发条件必须分两类：
- **count**：靠关键词命中条数判定，`threshold` 是条数（整数）。适合「某事件是否发生」。
- **value**：靠指标数值判定，`threshold` 是数值。适合「通胀≥X%」「收益率≥Y%」这类，
  系统只能提示人工读数，无法自动判定。
不要用 count 类型表达数值指标，否则会出现「命中 12 条」这种无意义告警。

## 7. 反面证据与证伪条件
什么证据出现时，这份备忘录的结论应当被推翻。

## 8. 复盘日期
具体日期，以及届时需要回答的 2 个问题。

硬规则：
- 概率必须合计 100；没有依据的数字标注「估计」。
- 不编造来源、日期、机构；材料不足就写「材料不足」。
- 建议必须可执行，不能是「密切关注」这类空话。

最后输出一个 JSON 代码块（```json ... ```），字段：
{{"scenarios":[{{"name":"...","prob":40}}],"triggers":[{{"id":"t1","description":"...","keywords":["..."],"threshold":3,"type":"count|value","action":"..."}}],"confidence":"low|medium|high","review_date":"YYYY-MM-DD"}}
"""


def material(items):
    lines = []
    for i, it in enumerate(items, 1):
        summary = (it.get("summary") or "")[:200].replace("\n", " ")
        lines.append(f"[{i}] ({it['source']} · {it['published'][:10]}) {it['title']} — {summary}")
    return "\n".join(lines)


def extract_json(text):
    m = re.search(r"```json\s*(.*?)```", text, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--horizon", type=int, default=90)
    ap.add_argument("--items", type=int, default=40)
    ap.add_argument("--slug", default="")
    ap.add_argument("--out-dir", default=os.path.join(HERE, "out", "decisions"))
    args = ap.parse_args()

    items = store.search(args.question, limit=args.items, days=args.days)
    if not items:
        print("no intel available; run collect first", file=sys.stderr)
        return 2

    prompt = TEMPLATE.format(question=args.question, horizon=args.horizon, days=args.days,
                             n=len(items), material=material(items))
    body = llm.chat(prompt, system=SYSTEM, timeout=480, max_tokens=16000)

    now = datetime.now(timezone.utc)
    slug = args.slug or hashlib.sha1(args.question.encode()).hexdigest()[:6]
    name = f"{now.strftime('%Y-%m-%d')}-{slug}"
    os.makedirs(args.out_dir, exist_ok=True)

    meta = extract_json(body)
    meta.update({
        "question": args.question,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "horizon_days": args.horizon,
        "items_used": len(items),
        "sources": sorted({it["source"] for it in items}),
    })
    json.dump(meta, open(os.path.join(args.out_dir, name + ".json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    header = ("---\n"
              f"question: {args.question}\n"
              f"generated_at: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
              f"horizon_days: {args.horizon}\n"
              f"items_used: {len(items)}\n"
              "---\n\n")
    open(os.path.join(args.out_dir, name + ".md"), "w", encoding="utf-8").write(
        header + body.strip() + "\n")
    print(f"written: {name}.md  scenarios={len(meta.get('scenarios') or [])} "
          f"triggers={len(meta.get('triggers') or [])}", flush=True)
    render.build()
    return 0


if __name__ == "__main__":
    sys.exit(main())
