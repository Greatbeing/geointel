#!/bin/bash
# run-market-collect.sh — 抓一轮市场硬数据写 market_series；库不在 git 里，不用发布。
set +e
cd /data/workspace/geointel || exit 1
python3 market_collect.py "$@" 2>&1 | tee /tmp/geointel-market.log
rc=${PIPESTATUS[0]}
python3 health.py record market $rc --note "$(tail -c 400 /tmp/geointel-market.log 2>/dev/null)" >/dev/null 2>&1
# 健康检查（任务失败 / 市场数据断更）跟着每日市场任务跑；状态变化时才推飞书
python3 health.py check --send
exit $rc
