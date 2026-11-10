#!/bin/bash
# install-geointel.sh — 在云端机部署全球情报 + 决策分析系统
set +e
ROOT=/data/workspace/geointel
SRC=/tmp/geointel

echo "=== 1. 目录与文件 ==="
mkdir -p "$ROOT"/{data,out/digest,out/decisions,out/alerts,site,logs}
cp "$SRC"/*.py "$SRC"/*.sh "$SRC"/*.json "$ROOT"/ 2>/dev/null
cp "$SRC"/geointel-*.service "$SRC"/geointel-*.timer /tmp/ 2>/dev/null
chmod +x "$ROOT"/*.sh
ls -la "$ROOT" | head -25

echo ""
echo "=== 2. 语法检查 ==="
python3 -m py_compile "$ROOT"/{store,collect,llm,digest,decide,watch,site}.py && echo "python OK"
for f in "$ROOT"/*.sh; do bash -n "$f" || echo "SYNTAX FAIL $f"; done
echo "bash OK"

echo ""
echo "=== 3. 仓库 ==="
cd "$ROOT" || exit 1
if [ ! -d .git ]; then
  git init -q -b main
  git remote add origin https://github.com/Greatbeing/geointel.git
fi
cat > .gitignore <<'EOF'
data/
logs/
__pycache__/
*.pyc
EOF
if ! gh repo view Greatbeing/geointel >/dev/null 2>&1; then
  gh repo create Greatbeing/geointel --private --description "Global intelligence + decision analysis system" 2>&1 | tail -2
fi
git add -A
git -c user.name=geointel -c user.email=geointel@local commit -q -m "scaffold: geointel (collect/digest/decide/watch/site)" 2>&1 | tail -2
git push -u origin main 2>&1 | tail -3
echo "HEAD: $(git rev-parse --short HEAD)  remote: $(git remote get-url origin)"

echo ""
echo "=== 4. systemd ==="
mkdir -p /data/home/.config/systemd/user
cp /tmp/geointel-collect.service /tmp/geointel-collect.timer \
   /tmp/geointel-digest.service  /tmp/geointel-digest.timer \
   /tmp/geointel-watch.service   /tmp/geointel-watch.timer \
   /data/home/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now geointel-collect.timer geointel-digest.timer geointel-watch.timer 2>&1 | tail -3
systemctl --user list-timers geointel-* --all --no-legend 2>/dev/null

echo ""
echo "=== 5. 首轮抓取 ==="
timeout 420 "$ROOT/run-collect.sh" 2>&1 | tail -40
echo "--- 库存 ---"
python3 - <<'PY'
import sys; sys.path.insert(0, "/data/workspace/geointel")
import store
print(store.stats())
PY
echo "=== DONE ==="
