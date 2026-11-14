#!/bin/bash
# run-decide.sh — 生成决策备忘录并发布
# 用法：
#   run-decide.sh "问题" [--days 30] [--horizon 90] [--slug x]
#   run-decide.sh --questions questions.json
set +e
cd /data/workspace/geointel || exit 1

if [ "$1" = "--questions" ]; then
  python3 decide.py --questions "$2"
  rc=$?
  count=$(python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(len(d.get('questions',[])) if isinstance(d,dict) else len(d))" "$2" 2>/dev/null)
  [ $rc -eq 0 ] && ./publish.sh "decisions: ${count:-?} questions"
  exit $rc
fi

python3 decide.py "$@"
rc=$?
[ $rc -eq 0 ] && ./publish.sh "decision: $1"
exit $rc