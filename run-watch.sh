#!/bin/bash
# run-watch.sh — 评估触发条件并发布
set +e
cd /data/workspace/geointel || exit 1
python3 watch.py "$@" 2>&1 | tee /tmp/geointel-watch.log
rc=${PIPESTATUS[0]}
python3 health.py record watch $rc --note "$(tail -c 400 /tmp/geointel-watch.log 2>/dev/null)" >/dev/null 2>&1
[ $rc -eq 0 ] && ./publish.sh "watch: $(date -u +%F)"
exit $rc
