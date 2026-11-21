#!/bin/bash
# run-market-collect.sh — 抓一轮市场硬数据写 market_series；库不在 git 里，不用发布。
set +e
cd /data/workspace/geointel || exit 1
python3 market_collect.py "$@"
exit $?
