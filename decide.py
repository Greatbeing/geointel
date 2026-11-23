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
import glob
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

import llm
import memo
import render
import score as scoring
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

最新硬数据（市场序列，数值与日期以表为准）：
{market_table}

**输出顺序硬要求**：先输出 JSON 代码块，再输出完整备忘录。
（JSON 在最前，即使后文被截断，结构数据也一定已给出。）

第一步，输出一个 JSON 代码块（```json ... ```），字段：
{{"scenarios":[{{"name":"...","prob":40}}],"triggers":[{{"id":"t1","description":"...","keywords":["..."],"keyword_groups":[["..."],["..."]],"exclude_keywords":["..."],"threshold":3,"type":"count|value","action":"...","series":"DFF","op":">=","window":1}}],"confidence":"low|medium|high","review_date":"YYYY-MM-DD"}}
触发条件里的 series / op / window 只用于 value 型：series 是市场序列名
（如 DFF、DGS10、USDCNY），op 是 >= / > / <= / < / ==（默认 >=），
window 是最近几条观测都要满足（默认 1）。
数值类触发条件若对应硬数据表里的序列，必须写上这三个字段，让系统能自动判定；
实在对应不上的才留空，系统会提示人工读数。
count 型必须给 keyword_groups：组内关键词是 OR（同义表达），组间是 AND
（必须同时成立的概念）。例如“盟友采纳出口管制”应写成
[["Netherlands","Japan","South Korea"],["adopts","aligns","implements"],
["export control","export restriction"]]，而不是把三组词平铺后任中一词。
keywords 保留为各组词的并集，供旧工具读取；exclude_keywords 用于排除已知的
同词异义噪声，没有就写 []。任一排除词命中即排除该材料，宁缺毋滥。

第二步，紧接着输出完整备忘录，严格按以下结构（中文）：

# {question}

## 1. 问题与边界
说明这个决策真正要选的是什么、不包含什么、最晚什么时候必须决定。

## 2. 已知事实
先贴一张硬数据快照表：从上面“最新硬数据”里挑与本题相关的行，照抄数值和日期，
不要改数字、不要写表里没有的数。再列 5–8 条事实，每条格式：事实 ——（来源，日期）[n]，
n 是上面材料列表里的编号。只写材料里有的。

## 3. 关键不确定性
3–5 个变量。每个写：变量名、当前区间、判断依据、为什么它重要。

## 4. 情景
3 个互斥情景，给出概率（整数，合计 100）与每个情景下的关键观察点。
每个情景的关键观察点必须锚定到材料编号 [n] 或硬数据表里的具体数值，不能空写。

## 5. 选项评估
至少 3 个选项（含「不做决定」）。每个写：预期收益、最坏情况、可逆性、执行成本。

## 6. 建议与触发条件
明确推荐一个选项，并给 2–4 个触发条件：可观测指标 + 阈值 + 触发后的动作。
触发条件必须分两类：
- **count**：靠关键词命中条数判定，`threshold` 是条数（整数）。适合「某事件是否发生」。
  必须用 `keyword_groups` 拆开行为主体、动作和事件对象，避免「日本教育新闻」
  仅因含 Japan 就触发出口管制动告警；同义替代词才放同一组。
- **value**：靠指标数值判定，`threshold` 是数值。适合「通胀≥X%」「收益率≥Y%」这类；
  对应硬数据表里序列的，必须写 series / op / window，让系统自动判定，
  只有对应不上的才留空走人工读数。
不要用 count 类型表达数值指标，否则会出现「命中 12 条」这种无意义告警。

## 7. 反面证据与证伪条件
什么证据出现时，这份备忘录的结论应当被推翻。

## 8. 复盘日期
具体日期，以及届时需要回答的 2 个问题。

## 9. 历史周期代价对照
找 1–2 个可比的历史 episode（如 2022 年激进加息周期、2013 年 taper tantrum、
2008 年金融危机、1994 年加息周期），写清当时发生了什么、代价是什么
（利率走了多少、利差扩了多少、持续了多久、谁付了代价），以及对当前决策的启示。
没有可靠材料支撑的 episode 就写「材料不足」，不编数字。

硬规则：
- 概率必须合计 100；没有依据的数字标注「估计」。
- 硬数据表里的数字是唯一可用的市场数值；写到备忘录里的利率/汇率必须与表一致，
  不得凭记忆写，不得把表里没有的数写成事实。
- 不编造来源、日期、机构；材料不足就写「材料不足」。
- 建议必须可执行，不能是「密切关注」这类空话。
- JSON 块与备忘录正文中的情景、触发条件必须一致。
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


def valid_meta(meta):
    """结构完整性：至少 2 个情景、至少 1 个触发条件，且不是「情报不足」的退化输出。"""
    scen = meta.get("scenarios") or []
    trig = meta.get("triggers") or []
    if len(scen) < 2 or not trig:
        return False
    names = " ".join(str(s.get("name", "")) for s in scen if isinstance(s, dict))
    return not ("情报不足" in names or "无法评估" in names)


def market_table():
    """最新硬数据表（market_series），供模板注入。没有数据时返回一句说明，不编数字。"""
    try:
        import market_collect
        latest = store.latest_market()
    except Exception:  # noqa: BLE001 — 市场表不可用也不阻断备忘录生成
        return "（市场数据暂不可用，以下备忘录不得引用任何利率/汇率数值。）"
    if not latest:
        return "（市场数据暂不可用，以下备忘录不得引用任何利率/汇率数值。）"
    lines = ["| 指标 | 最新值 | 日期 | 来源 |", "|---|---|---|---|"]
    for name in ("DFF", "DGS2", "DGS10", "US210", "USDCNY", "EURUSD", "USDJPY", "DTWEXBGS"):
        row = latest.get(name)
        if not row:
            continue
        info = market_collect.SERIES[name]
        lines.append("| %s | %s | %s | %s |" % (
            info["label"], info["fmt"].format(row["value"]), row["date"], row["source"]))
    return "\n".join(lines)


def build_prompt(question, horizon, days, items):
    """拼一条备忘录的完整提示词：问题 + 情报材料 + 最新硬数据表。"""
    return TEMPLATE.format(question=question, horizon=horizon, days=days,
                           n=len(items), material=material(items),
                           market_table=market_table())


def auto_score_memo(memo_path, scores_path=None):
    """给 memo 评结构分并写入 scores.jsonl（Week 5）。

    生成时即登记结构分，review.py / calibration 页立即可用，不再靠
    事后手工补跑 score.py --all。失败向上抛，由调用方兜住：评分挂了
    不能把已经落盘的备忘录作废。scores_path 缺省用仓库根的 scores.jsonl，
    测试可注入临时路径。返回 (分数, 扣分列表)。
    """
    m = memo.load_memo(memo_path)
    s, deductions = scoring.structural_score(m)
    if scores_path:
        scoring.record_structural(m, s, deductions, path=scores_path)
    else:
        scoring.record_structural(m, s, deductions)
    return s, deductions


def run_question(q, out_dir, now):
    """生成一条备忘录，返回 (slug, ok, detail)。"""
    question = q["question"]
    days = int(q.get("days", 30))
    horizon = int(q.get("horizon", 90))
    items_n = int(q.get("items", 40))
    slug = q.get("slug") or hashlib.sha1(question.encode()).hexdigest()[:6]
    name = f"{now.strftime('%Y-%m-%d')}-{slug}"

    if os.path.exists(os.path.join(out_dir, name + ".md")):
        return slug, True, f"skip (exists: {name}.md)"

    items = store.search(question, limit=items_n, days=days)
    if not items:
        return slug, False, "no intel available; run collect first"

    prompt = build_prompt(question, horizon, days, items)
    # JSON 块已被挪到模板最前：即使正文被截断，结构数据也一定先落盘。
    # 仍留两档重试兜底：先 32k，拿不到 JSON 再 64k（给 reasoning 更多预算）。
    meta = {}
    for budget in (32000, 64000):
        body = llm.chat(prompt, system=SYSTEM, timeout=480, max_tokens=budget)
        meta = extract_json(body)
        if valid_meta(meta):
            break
    if not valid_meta(meta):
        return slug, False, "LLM output incomplete (scenarios<2 or no triggers)"
    meta.update({
        "question": question,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "horizon_days": horizon,
        "items_used": len(items),
        "sources": sorted({it["source"] for it in items}),
    })
    json.dump(meta, open(os.path.join(out_dir, name + ".json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    header = ("---\n"
              f"question: {question}\n"
              f"generated_at: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
              f"horizon_days: {horizon}\n"
              f"items_used: {len(items)}\n"
              "---\n\n")
    memo_path = os.path.join(out_dir, name + ".md")
    open(memo_path, "w", encoding="utf-8").write(header + body.strip() + "\n")
    detail = f"written: {name}.md  scenarios={len(meta.get('scenarios') or [])} " \
             f"triggers={len(meta.get('triggers') or [])}"
    # Week 5：落盘即评结构分；失败不阻断备忘录，只留提示。
    try:
        s, deductions = auto_score_memo(memo_path)
        detail += f"  structural={s}/100"
        for pts, reason in deductions:
            print(f"  {slug} 扣分 -{pts}：{reason}", flush=True)
    except Exception as e:  # noqa: BLE001
        detail += f"  scoring failed: {type(e).__name__} {e}"
    return slug, True, detail


def run_questions(path):
    """questions.json 模式：逐条生成，跳过已存在的，状态写回。"""
    qs = json.load(open(path, encoding="utf-8"))
    if isinstance(qs, dict):
        qs = qs.get("questions", [])
    out_dir = os.path.join(HERE, "out", "decisions")
    os.makedirs(out_dir, exist_ok=True)
    now = datetime.now(timezone.utc)
    results = []
    for q in qs:
        slug, ok, detail = run_question(q, out_dir, now)
        results.append({"slug": slug, "question": q["question"], "ok": ok, "detail": detail})
        print(f"  {slug}: {detail}", flush=True)
    json.dump(results, open(path + ".state.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    bad = [r for r in results if not r["ok"]]
    if bad:
        print(f"{len(results) - len(bad)}/{len(results)} ok, {len(bad)} failed", file=sys.stderr)
        return 2
    print(f"{len(results)}/{len(results)} ok", flush=True)
    render.build()
    return 0


def latest_days(out_dir):
    """slug -> 最新备忘录日期（用于轮转时挑最久没更新的问题）。"""
    latest = {}
    for path in glob.glob(os.path.join(out_dir, "*.json")):
        name = os.path.basename(path)[:-5]
        m = re.match(r"^(\d{4}-\d\d-\d\d)-(.+)$", name)
        day, slug = (m.group(1), m.group(2)) if m else ("", name)
        if slug not in latest or day > latest[slug]:
            latest[slug] = day
    return latest


def run_rotate(path):
    """每日轮转：只生成「最久没更新」的一个问题。

    五个问题分摊到每天，站点每天都有新备忘录，单次只占一个模型的生成时间。
    """
    qs = json.load(open(path, encoding="utf-8"))
    if isinstance(qs, dict):
        qs = qs.get("questions", [])
    if not qs:
        print("no questions", file=sys.stderr)
        return 2
    out_dir = os.path.join(HERE, "out", "decisions")
    os.makedirs(out_dir, exist_ok=True)
    latest = latest_days(out_dir)

    def age(q):
        slug = q.get("slug") or hashlib.sha1(q["question"].encode()).hexdigest()[:6]
        return latest.get(slug, "")

    q = min(qs, key=age)
    slug, ok, detail = run_question(q, out_dir, datetime.now(timezone.utc))
    print(f"  {slug}: {detail}", flush=True)
    if not ok:
        return 2
    render.build()
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="?")
    ap.add_argument("--questions", default="")
    ap.add_argument("--rotate", action="store_true",
                    help="配合 --questions：只生成最久没更新的一个问题")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--horizon", type=int, default=90)
    ap.add_argument("--items", type=int, default=40)
    ap.add_argument("--slug", default="")
    ap.add_argument("--out-dir", default=os.path.join(HERE, "out", "decisions"))
    args = ap.parse_args()

    if args.questions:
        return run_rotate(args.questions) if args.rotate else run_questions(args.questions)

    if not args.question:
        ap.error("question is required (or use --questions)")
    now = datetime.now(timezone.utc)
    slug, ok, detail = run_question(
        {"question": args.question, "days": args.days, "horizon": args.horizon,
         "items": args.items, "slug": args.slug}, args.out_dir, now)
    print(detail, flush=True)
    render.build()
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
