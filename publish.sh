#!/bin/bash
# publish.sh — 把 out/ 与 site/ 推到私有仓库；没有变化就静默退出。
set +e
cd /data/workspace/geointel || exit 1

git add -A 2>/dev/null
if git diff --cached --quiet; then
  echo "publish: nothing to commit"
  python3 health.py record publish 0 --note "nothing to commit" >/dev/null 2>&1
  exit 0
fi
git -c user.name="geointel" -c user.email="geointel@local" commit -q -m "${1:-update}" || exit 0
for i in 1 2 3; do
  git pull --rebase --autostash -q >/dev/null 2>&1
  if git push -q origin main >/dev/null 2>&1; then
    echo "publish: pushed $(git rev-parse --short HEAD)"
    python3 health.py record publish 0 >/dev/null 2>&1
    exit 0
  fi
  sleep 3
done
echo "publish: push failed"
python3 health.py record publish 1 --note "git push failed 3x" >/dev/null 2>&1
exit 1
