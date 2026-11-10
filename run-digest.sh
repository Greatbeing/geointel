#!/bin/bash
# run-digest.sh — 生成当日简报并发布
set +e
cd /data/workspace/geointel || exit 1
python3 digest.py "$@"
rc=$?
[ $rc -eq 0 ] && ./publish.sh "digest: $(date -u +%F)"
exit $rc
