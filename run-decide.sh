#!/bin/bash
# run-decide.sh — 生成决策备忘录并发布
# 用法：run-decide.sh "问题" [--days 30] [--horizon 90] [--slug x]
set +e
cd /data/workspace/geointel || exit 1
python3 decide.py "$@"
rc=$?
[ $rc -eq 0 ] && ./publish.sh "decision: $1"
exit $rc
