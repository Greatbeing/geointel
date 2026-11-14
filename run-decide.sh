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
  python3 decide.py --questions "$QFILE" "$@"
  rc=$?
  count=$(python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(len(d.get('questions',[])) if isinstance(d,dict) else len(d))" "$QFILE" 2>/dev/null)
  # 部分问题失败也要发布已写出的备忘录；rc 仍返回给 systemd 标记失败。
  ./publish.sh "decisions: ${count:-?} questions"
  exit $rc
fi

python3 decide.py "$@"
rc=$?
./publish.sh "decision: $1"
exit $rc