#!/bin/bash
# run-watch.sh — 评估触发条件并发布
set +e
cd /data/workspace/geointel || exit 1
python3 watch.py "$@"
rc=$?
[ $rc -eq 0 ] && ./publish.sh "watch: $(date -u +%F)"
exit $rc
