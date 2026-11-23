# geointel — 全球情报 + 决策分析系统

零第三方依赖（Python 标准库 + SQLite FTS5）的全球情报采集、每日简报、决策备忘录与触发条件监控。
跑在 Instabox 免费云机 `being` 上，`/data/workspace/geointel/`，由 systemd user timer 驱动。

## 流水线

```
sources.json (49 源，含多边金融/宏观政策)
      │
      ▼
collect.py ──► data/intel.db (SQLite + FTS5)
      │
      ├─► digest.py ──► out/digest/<date>.md|json ──┐
      ├─► decide.py ──► out/decisions/<date>-<slug>.md|json ──┤
      └─► watch.py  ──► out/alerts/<date>.md       ──┤
                                                     ▼
                                              render.py ──► docs/
                                                     │
                                                     ▼
                                              publish.sh ──► GitHub Pages (main /docs)
```

## 组件

| 文件 | 作用 |
|---|---|
| `sources.json` | 49 个 RSS/Atom/RDF 源（含多边开发银行与国际金融组织 `ifi` / `macro_policy`），含 region / category / weight |
| `collect.py` | 并发抓取、HTML 实体与 gzip 容错、URL 规范化去重、入库 |
| `store.py` | SQLite 存储 + FTS5 检索；中文问题经 `LEXICON` 映射成英文检索词；`market_series` 存市场硬数据 |
| `llm.py` | 统一走本机 magpie 网关 `127.0.0.1:3425`，`max_tokens>=16000`，预算翻倍重试 |
| `digest.py` | 每日简报：今日要点 / 区域动态 / 技术与产业 / 金融与政策 / 值得跟踪的信号；顶部贴利率/汇率仪表条 |
| `decide.py` | 决策备忘录：事实 / 不确定性 / 情景概率 / 选项评估 / 触发条件 / 证伪条件 / 复盘日期；落盘即自动评结构分 |
| `watch.py` | 读 `out/decisions/*.json` 的 triggers，统计命中并写告警；支持结构化关键词组、排除词与实时推送去重 |
| `market_collect.py` | 市场硬数据抓取（FRED / ECB / Frankfurter，零 key），供 value 型触发与仪表条 |
| `memo.py` / `deliver.py` | memo 解析与聊天版摘要（只读） |
| `score.py` / `review.py` | 结构分（确定性检查）+ 复核日 Brier 校准分；`docs/calibration.html` |
| `weekly_summary.py` | 每周汇总（飞书 Webhook，`--send` 需 URL） |
| `questions.py` / `questions.json` | 周度轮转的问题清单 |
| `render.py` | 把三类产物渲染成离线可读的静态站点（单文件 CSS，无外链） |
| `publish.sh` | `git add -A` + commit + push，无变化则静默退出 |

## 触发条件关键词（Week 5 告警可信度）

count 型触发器支持三档写法，向后兼容：

```json
{
  "id": "t2", "type": "count", "threshold": 2,
  "keywords": ["netherlands", "japan", "adopts", "export controls"],
  "keyword_groups": [
    ["netherlands", "japan", "south korea", "singapore"],
    ["adopts", "aligned with us", "implements"],
    ["export control", "export restrictions"]
  ],
  "exclude_keywords": ["education", "exam"]
}
```

- `keywords`（旧）：任一词命中即计数——简单，但 "Japan 教育部丑闻" 也会给
  "盟友采纳管制" 凑数，2026-11-23 的告警已出现这类误报。
- `keyword_groups`（推荐）：组内任一（同义表达），组间全部（必须同一篇材料
  同时成立）。行为主体 / 动作 / 事件对象各占一组，单篇社会新闻不可能凑齐三组。
- `exclude_keywords`：任一命中即排除该材料（"education" 挡住教育部新闻）。
- ASCII 关键词按词边界匹配并兼容复数（`export control` 命中 `export controls`）；
  中日韩关键词按子串匹配。

value 型触发器：绑定了 `series` 的按 `market_series` 数值自动判定；
没绑定的不再被静默丢弃，统一进入告警页「需人工读数」清单。

## 实时告警推送（可选）

```bash
GEOINTEL_ALERT_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/xxx \
  python3 watch.py --hours 24 --send
```

- 只推「上次未活跃、这次活跃」的触发器；持续活跃的告警不重复打扰，
  活跃解除后再次触发会重新通知（去重状态在 `data/alert-state.json`，不入库不入站）。
- 发送失败不推进状态，下一轮自动重试；`run-collect.sh` 检测到环境变量会自动加 `--send`。
- 站点 `docs/alerts.html` 始终保留当日全部活跃告警，与推送互不影响。


## 用法

```bash
# 手动跑一轮采集 + 触发评估
./run-collect.sh

# 每日简报
./run-digest.sh

# 决策备忘录
./run-decide.sh "未来 90 天半导体出口管制升级的概率与应对" --horizon 90 --slug chips

# 触发条件
./run-watch.sh
```

## 定时

| timer | 周期 | 作用 |
|---|---|---|
| `geointel-collect.timer` | 每 2 小时 | 采集 + 触发评估 + 发布 |
| `geointel-digest.timer` | 00:20 UTC | 每日简报 |
| `geointel-watch.timer` | 00:45 UTC | 独立触发评估（兜底） |
| `geointel-decide.timer` | 每天 01:00 UTC | 轮转生成一条决策备忘录（选最久没更新的问题；批量用 `run-decide.sh --questions questions.json`） |

同一个 slug 周度重跑会生成新日期的新备忘录，旧文件保留可回看；
`watch.py` 只读每个 slug 最新一份的 triggers，`render.py` 的索引也只列最新一条，
避免同一触发条件重复告警、首页重复堆叠。

## 已知约束

- **只基于抓取材料**：LLM 被要求不得补充材料之外的事实；材料不足时写「材料不足」。
- **免费模型多为推理模型**：`reasoning_content` 先吃 token，`max_tokens` 太小会导致正文为空。
  所有调用统一给 16000，`finish_reason=length` 时翻倍重试，仍失败换模型。
- **中文检索**：语料全英文，中文决策问题必须经 `LEXICON` 映射，否则 FTS 零命中。
- **云机会挂起**：空闲 30 分钟挂起，timer 不会唤醒机器；`Persistent=true` 保证唤醒后补跑。
- **站点已公开**：仓库 2026-11-11 转为 public，Pages 从 `main` 的 `/docs` 构建。
  页面根路径链接由 `render.py` 的 `BASE` 控制（默认 `/geointel`）。

## 版式

站点是给人读的报告，不是给机器看的仪表盘。三条规矩：

1. **只放读者需要的信息**。运维数字（`源状态 37/37`、窗口/库存条数、生成耗时、触发 ID、命中计数、
   英文线索词）一律不上页面；`strip_ops()` 删运维行，`strip_data_blocks()` 删备忘录末尾的机读 JSON
   （同样内容已在正文里用自然语言写过）。
2. **出处压淡**：句末 `（来源，日期）` 由 `SRC_RE` 包成 `.src`，字号 0.84em、灰色，不抢正文。
3. **概率可读**：表格里表头含「概率」的列自动渲染成数字 + 发丝量度线（`--p` 变量），
   50% 就是半条线，不需要换算。

排版：单栏 720px、衬线标题（思源宋体/Songti/SimSun）、正文首行缩进 2em、
发丝线分节、告警条目改为「可点标题 + 小字来源」并跨链回备忘录。深色模式与打印样式都覆盖。