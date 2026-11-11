#!/bin/bash
# run-collect.sh — 抓取一轮，然后评估决策触发条件；有变化才发布。
set +e
cd /data/workspace/geointel || exit 1
python3 collect.py "$@"
rc=$?
# 触发条件是纯 SQLite 统计，没有 LLM 成本，跟着每次抓取一起跑
python3 watch.py --hours 24
if [ $? -eq 0 ]; then
  ./publish.sh "collect+watch: $(date -u +%F)"
fi
exit $rc
