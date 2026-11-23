<div align="center">

# geointel

**全球情报 · 决策备忘录 · 预测复盘**

每天读完 49 个信源，写一份简报、一份带概率的决策备忘录，<br>
盯住触发条件，到期用 Brier 分数给自己打分。

[在线阅读](https://greatbeing.github.io/geointel/) ·
[预测战绩](https://greatbeing.github.io/geointel/track-record.html) ·
[决策备忘录](https://greatbeing.github.io/geointel/decisions.html) ·
[告警](https://greatbeing.github.io/geointel/alerts.html) ·
[校准](https://greatbeing.github.io/geointel/calibration.html)

[![Tests](https://github.com/Greatbeing/geointel/actions/workflows/tests.yml/badge.svg)](https://github.com/Greatbeing/geointel/actions/workflows/tests.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-3b5b7a)
![Dependencies](https://img.shields.io/badge/dependencies-0-5a7a5a)

</div>

---

## 它做什么

> 不是新闻聚合器。是一个会对自己的判断负责的分析员。

- **只说材料里有的**。每个结论都附原文链接与抓取时间；材料不足就写「材料不足」。
- **给概率，不给形容词**。备忘录写清情景概率、触发条件、证伪条件和复盘日期。
- **盯住信号**。触发条件按关键词组粗筛，可选 LLM 逐条语义复核，命中推送飞书。
- **到期认账**。复核日自动结算 Brier 分数，战绩页公开所有预测与结果。

## 流水线

```
sources.json  49 个 RSS / Atom 源（地缘、产业、多边金融、宏观政策）
     │
     ▼
collect.py ─► SQLite + FTS5
     │
     ├─ digest.py ─► 每日简报
     ├─ decide.py ─► 决策备忘录（情景概率 / 触发 / 证伪 / 复盘日）
     ├─ watch.py  ─► 触发告警 ─► 飞书
     └─ review.py ─► Brier 校准
     │
     ▼
render.py ─► docs/ ─► GitHub Pages
```

零第三方依赖：Python 标准库 + SQLite FTS5。LLM 统一走本机 magpie 网关 `127.0.0.1:3425`。

## 快速开始

```bash
./run-collect.sh                      # 采集 + 触发评估
./run-digest.sh                       # 每日简报
./run-decide.sh "未来 90 天半导体出口管制升级的概率与应对" --horizon 90 --slug chips
./run-watch.sh                        # 触发条件
python3 render.py                     # 生成静态站
python3 -m unittest discover -s tests # 测试
```

## 组件

| 文件 | 作用 |
|---|---|
| `collect.py` | 并发抓取、容错解析、URL 规范化去重；末尾汇总失败源 |
| `store.py` | SQLite + FTS5；中文问题经 `LEXICON` 映射为英文检索词 |
| `market_collect.py` | 利率 / 汇率硬数据（FRED、ECB、Frankfurter，免 key） |
| `digest.py` | 每日简报：要点、区域、产业、金融政策、待跟踪信号 |
| `decide.py` | 决策备忘录，落盘即评结构分 |
| `watch.py` | 触发条件评估：关键词组、排除词、LLM 复核、推送去重 |
| `score.py` · `review.py` | 结构分 + 复核日 Brier 校准 |
| `render.py` | 离线可读的静态站，单文件 CSS，无外链 |
| `llm.py` | magpie 网关客户端，预算翻倍重试，失败换模型 |

## 触发条件

```json
{
  "id": "t2", "type": "count", "threshold": 2,
  "keyword_groups": [
    ["netherlands", "japan", "south korea"],
    ["adopts", "aligned with us", "implements"],
    ["export control", "export restrictions"]
  ],
  "exclude_keywords": ["education", "exam"]
}
```

组内任一、组间全部——主体、动作、对象必须在同一篇材料里同时成立。
`exclude_keywords` 任一命中即排除。ASCII 词按词边界匹配并兼容复数，中日韩词按子串匹配。
旧式 `keywords`（任一命中即计数）仍兼容。

`value` 型触发器绑定 `series` 时按市场数据自动判定，否则进入「需人工读数」清单。

## 配置

| 环境变量 | 作用 | 默认 |
|---|---|---|
| `GEOINTEL_WATCH_LLM_VERIFY` | `1` 开启告警 LLM 语义复核（等价 `--llm-verify`）；LLM 异常时降级并标「未复核」 | 关闭 |
| `GEOINTEL_ALERT_WEBHOOK` | 飞书机器人 Webhook；只推新激活的触发器，失败下轮重试 | 不设 |

## 运行

跑在 Instabox 云机，systemd user timer 驱动：

| timer | 周期 | 作用 |
|---|---|---|
| `geointel-collect` | 每 2 小时 | 采集 + 触发评估 + 发布 |
| `geointel-digest` | 00:20 UTC | 每日简报 |
| `geointel-watch` | 00:45 UTC | 触发评估兜底 |
| `geointel-decide` | 01:00 UTC | 轮转生成一条备忘录 |

云机空闲 30 分钟挂起，`Persistent=true` 保证唤醒后补跑。
同一 slug 重跑生成新日期文件，旧版保留；告警与索引只读每个 slug 的最新一份。

## 版式

站点是给人读的报告，不是仪表盘。

1. **只放读者需要的**：运维数字与机读 JSON 不上页面。
2. **出处压淡**：句末来源小字灰色，不抢正文。
3. **概率可读**：概率列渲染为数字 + 发丝量度线，50% 就是半条线。

单栏 720px，衬线标题，首行缩进，发丝线分节；深色模式与打印样式齐备。

## 约束

- LLM 不得补充材料之外的事实。
- 免费推理模型先消耗 `reasoning_content`，所有调用 `max_tokens ≥ 16000`，截断时翻倍重试。
- 语料以英文为主，中文问题必须经 `LEXICON` 映射，否则检索为空。

---

<div align="center"><sub>判断要写下来，才能被检验。</sub></div>
