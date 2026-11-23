#!/bin/bash
# run-collect.sh — 抓取一轮，然后评估决策触发条件；有变化才发布。
# 配置了 GEOINTEL_ALERT_WEBHOOK 时，watch.py 会把「新触发」的告警实时推飞书
# （活跃告警去重，见 data/alert-state.json）；没配就保持纯站点报告。
set +e
cd /data/workspace/geointel || exit 1
python3 collect.py "$@"
rc=$?
# 触发条件是纯 SQLite 统计，没有 LLM 成本，跟着每次抓取一起跑
WATCH_SEND=()
[ -n "$GEOINTEL_ALERT_WEBHOOK" ] && WATCH_SEND=(--send)
python3 watch.py --hours 24 "${WATCH_SEND[@]}"
if [ $? -eq 0 ]; then
  ./publish.sh "collect+watch: $(date -u +%F)"
fi
exit $rc
