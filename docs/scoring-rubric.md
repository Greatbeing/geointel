# geointel 决策 memo 评分标准（Scoring Rubric）

目的：让每份 memo 的好坏可衡量。分两层——**结构分**在出 memo 当天就能评，
**结果分**要等复核日期到了才评。两层都记在 `scores.jsonl` 里，一行一个 memo。

> 原则：结构分是**确定性检查**（解析 memo 的 JSON 块和正文），不调 LLM。
> 需要 LLM 或人工判断的部分（触发条件写得够不够具体、情景是否互斥完备等）
> 列在文末“人工复核项”，不进自动分，也不为打分新接模型调用。

## (a) 结构分（满分 100）

| # | 项 | 分 | 判定规则 |
|---|----|----|----------|
| 1 | 概率完整性 | 20 | 情景 ≥2 个（少于 2 个扣 10）；每个情景有名字和概率（缺一项扣 5，最多扣 10）；概率和 =100（允差 ±1，否则扣 10） |
| 2 | 触发条件可证伪 | 40 | 以 4 条触发器为满分基准，每条满分 10 分：描述 ≥10 字（3 分）+ 有数值阈值（3 分）+ 有对应动作（2 分）+ 可观测口径（2 分：count 型需 ≥1 个关键词，value 型需描述里含数字或百分号）。触发器少于 4 条时，每少一条上限少 10 分，记为一条扣分原因（不静默丢分）；没有触发器 0 分 |
| 3 | 置信度 | 10 | confidence 在 low / medium / high 内，否则 0 分 |
| 4 | 复核日期 | 15 | 是合法日期（5 分）+ 晚于生成日期（5 分）+ 与期限一致（复核日 − 生成日与 horizon_days 相差 ≤3 天，5 分） |
| 5 | 最晚决策点 | 10 | 正文写了“最晚决策点”且带日期（10 分）；只写了词没给日期（5 分）；没写（0 分） |
| 6 | 证据基础 | 5 | items_used ≥10 条（5 分）；1–9 条（2 分）；缺失（0 分） |

跑法：`python3 score.py <memo.md>`，扣分原因会逐条打印。

**结构分只管“格式写没写对”，不管“判断得准不准”。** 一份 100 分的 memo
仍可能押错方向——准不准看下面的结果分。

## (b) 结果分（复核日期到了才评）

复核那天，人工判定实际走向落在哪一个情景（或不在任何一个里），然后跑：

```
python3 score.py --outcome <memo.md> --occurred "情景名"
python3 score.py --outcome <memo.md> --occurred none   # 实际走向不在情景里
```

**Brier 分数**（越低越好，范围 0–2）：

```
Brier = (1/N) × Σ (pᵢ − oᵢ)²
```

N 为情景数，pᵢ 为第 i 个情景的预测概率（0–1），oᵢ = 1（实际发生）或 0（没发生）。

换算成 0–100 的**校准分**：`校准分 = 100 × (1 − Brier/2)`，越高越好。
同时记录**最高概率情景是否命中**（hit / miss）。

**算例**（2026-11-21 芯片 memo，概率 45/35/20）：
- 若“加速收紧”（45%）发生：Brier = ((0.45−1)² + 0.35² + 0.20²)/3 = 0.155，校准分 92.2，最高概率命中。
- 若“管控常态化”（35%）发生：Brier = (0.45² + (0.35−1)² + 0.20²)/3 = 0.222，校准分 88.9，最高概率未命中。

**注意 Brier 的一个 quirks**：它奖励分散预测。上面三个数全押错时
（实际走向不在情景里，o 全为 0），Brier = 0.122，比押中最高概率情景的
0.155 还“好看”。所以**实际走向不在情景里必须单独记为“情景覆盖失效”
（coverage failure）**，不能只看 Brier 数字——这比单个概率押错更严重，
说明当初的情景集合本身漏了关键可能。

## 人工复核项（不进自动分）

复核日人工过一遍，记在 memo 对应的复核记录里：

1. 触发条件写得够不够具体（能不能不靠解释就判定触发了没有）？
2. 情景之间是否互斥、是否覆盖了主要可能（有没有事后看很明显却漏掉的走向）？
3. 建议是否真的挂在触发条件上（触发了就知道该干什么）？
4. 正文引用的事实是否可溯源（来源、日期对得上）？

## 与四周路线图的关系（Week 4 输入层）

本评分系统是路线图 Week 4（review.py 自动复盘 + docs/calibration.html）的
**输入层**，不是另一套复盘系统：

- `scores.jsonl` 每行已留好 review.py 要用的字段：`memo`（文件名）、`slug`
  （去掉日期前缀的 memo 标识）、`review_date`、结构分、结果分（复核日前为
  `null`，等复核日再填）。
- Week 4 已做完（2026-11-22）：`review.py` 直接读 `scores.jsonl`，
  到期未复盘的列出来等人确认实际走向，确认后登记结果分，
  `--build-page` 生成 `docs/calibration.html`（站内导航有“概率校准”入口）；
  每周汇总推送由 `weekly_summary.py` 实现（默认只打印，`--send` 需 Webhook URL）。

## 复核流程（Week 4：review.py）

## 记录文件 scores.jsonl

每行一个 memo（按 memo 去重更新，结构分重跑会覆盖，结果分保留）：

```json
{"memo": "2026-11-21-chips-export-control", "slug": "chips-export-control",
 "memo_date": "2026-11-21",
 "question": "未来 90 天半导体出口管制升级的概率与应对",
 "generated_at": "2026-11-21T04:11:02Z", "review_date": "2027-02-18",
 "structural": {"score": 100, "deductions": []},
 "outcome": null, "scored_at": "2026-11-22T..."}
```

`outcome` 在复核日前为 `null`；复核后记 `occurred / brier / calibration /
top_hit / coverage_failure / scored_at`。

## 复核流程（Week 4：review.py）

1. 到了某份 memo 的 `review_date`，人工判定实际走向。
2. 跑 `python3 review.py --memo <memo.md> --occurred "情景名"`（或 `none`）
   登记结果分；`python3 review.py` 随时看到期/将到期清单。
3. 跑 `python3 review.py --build-page` 刷新 `docs/calibration.html`。
4. 攒够 5–10 份有结果分的 memo 后，可以看平均校准分和最高概率命中率，
   判断这套 memo 的概率是不是系统性偏高或偏低——那时再谈调 decide 的提示词。

## 每周汇总（Week 4：weekly_summary.py）

- `python3 weekly_summary.py` 打印本周汇总（新 memo、将到期、已逾期、校准快照）。
- `--send` 需 Webhook URL（`--webhook <URL>` 或环境变量
  `GEOINTEL_WEEKLY_WEBHOOK`）；没给就停下并明说缺什么，不猜地址。
- 邮件渠道没接：需要用户给收件地址 + 发件配置。
