#!/bin/bash
# run-digest.sh — 生成当日简报并发布
set +e
cd /data/workspace/geointel || exit 1
python3 digest.py "$@" 2>&1 | tee /tmp/geointel-digest.log
rc=${PIPESTATUS[0]}
python3 health.py record digest $rc --note "$(tail -c 400 /tmp/geointel-digest.log 2>/dev/null)" >/dev/null 2>&1
# 到期复盘草稿（只建议，不计分；已有草稿跳过，所以每天跑也只推一次）
python3 review_draft.py --ahead 3 --send 2>&1 | tail -5
[ $rc -eq 0 ] && ./publish.sh "digest: $(date -u +%F)"
exit $rc
