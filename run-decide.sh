#!/bin/bash
# run-decide.sh — 生成决策备忘录并发布
# 用法：
#   run-decide.sh "问题" [--days 30] [--horizon 90] [--slug x]
#   run-decide.sh --questions questions.json
set +e
cd /data/workspace/geointel || exit 1

if [ "$1" = "--questions" ]; then
  QFILE="$2"
  shift 2
  python3 decide.py --questions "$QFILE" "$@" 2>&1 | tee /tmp/geointel-decide.log
  rc=${PIPESTATUS[0]}
  python3 health.py record decide $rc --note "$(tail -c 400 /tmp/geointel-decide.log 2>/dev/null)" >/dev/null 2>&1

  count=$(python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(len(d.get('questions',[])) if isinstance(d,dict) else len(d))" "$QFILE" 2>/dev/null)
  # 知识库：从当天条目与备忘录增量维护词条（失败不影响发布），再重建站点
  python3 kb.py update --max-new 10 2>&1 | tail -3
  python3 health.py record kb ${PIPESTATUS[0]} >/dev/null 2>&1
  python3 render.py >/dev/null 2>&1
  # 部分问题失败也要发布已写出的备忘录；rc 仍返回给 systemd 标记失败。
  ./publish.sh "decisions: ${count:-?} questions"
  exit $rc
fi

python3 decide.py "$@"
rc=$?
./publish.sh "decision: $1"
exit $rc