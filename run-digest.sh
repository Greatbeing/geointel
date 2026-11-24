#!/bin/bash
# run-digest.sh — 生成当日简报并发布
set +e
cd /data/workspace/geointel || exit 1
python3 digest.py "$@" 2>&1 | tee /tmp/geointel-digest.log
rc=${PIPESTATUS[0]}
python3 health.py record digest $rc --note "$(tail -c 400 /tmp/geointel-digest.log 2>/dev/null)" >/dev/null 2>&1
[ $rc -eq 0 ] && ./publish.sh "digest: $(date -u +%F)"
exit $rc
