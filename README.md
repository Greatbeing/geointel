# geointel — 全球情报 + 决策分析系统

零第三方依赖（Python 标准库 + SQLite FTS5）的全球情报采集、每日简报、决策备忘录与触发条件监控。
跑在 Instabox 免费云机 `being` 上，`/data/workspace/geointel/`，由 systemd user timer 驱动。

## 流水线

```
sources.json (37 源)
      │
      ▼
collect.py ──► data/intel.db (SQLite + FTS5)
      │
      ├─► digest.py ──► out/digest/<date>.md|json ──┐
      ├─► decide.py ──► out/decisions/<date>-<slug>.md|json ──┤
      └─► watch.py  ──► out/alerts/<date>.md       ──┤
                                                     ▼
                                              render.py ──► site/
                                                     │
                                                     ▼
                                              publish.sh ──► GitHub (main)
```

## 组件

| 文件 | 作用 |
|---|---|
| `sources.json` | 37 个 RSS/Atom/RDF 源，含 region / category / weight |
| `collect.py` | 并发抓取、HTML 实体与 gzip 容错、URL 规范化去重、入库 |
| `store.py` | SQLite 存储 + FTS5 检索；中文问题经 `LEXICON` 映射成英文检索词 |
| `llm.py` | 统一走本机 magpie 网关 `127.0.0.1:3425`，`max_tokens>=16000`，预算翻倍重试 |
| `digest.py` | 每日简报：今日要点 / 区域动态 / 技术与产业 / 金融与政策 / 值得跟踪的信号 |
| `decide.py` | 决策备忘录：事实 / 不确定性 / 情景概率 / 选项评估 / 触发条件 / 证伪条件 / 复盘日期 |
| `watch.py` | 读 `out/decisions/*.json` 的 triggers，统计命中并写告警 |
| `render.py` | 把三类产物渲染成离线可读的静态站点（单文件 CSS，无外链） |
| `publish.sh` | `git add -A` + commit + push，无变化则静默退出 |

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

## 已知约束

- **只基于抓取材料**：LLM 被要求不得补充材料之外的事实；材料不足时写「材料不足」。
- **免费模型多为推理模型**：`reasoning_content` 先吃 token，`max_tokens` 太小会导致正文为空。
  所有调用统一给 16000，`finish_reason=length` 时翻倍重试，仍失败换模型。
- **中文检索**：语料全英文，中文决策问题必须经 `LEXICON` 映射，否则 FTS 零命中。
- **云机会挂起**：空闲 30 分钟挂起，timer 不会唤醒机器；`Persistent=true` 保证唤醒后补跑。
- **站点当前是私有仓库**：GitHub Pages 需要公开仓库或 Pro，所以现在只能离线/本地读。
