#!/usr/bin/env python3
"""geointel Tier-1 patch — run ON being in /data/workspace/geointel.
1. sources.json: WTO URL -> working feed; Brookings (dead) -> CFR (verified 24 items).
2. collect.py: reject HTML pages with a clear error instead of cryptic ParseError.
3. digest.py: append source-health (ok/total + failed list) to header and body.
4. geointel-collect.timer: add Persistent=true so missed runs catch up on boot.
All edits are exact string replacements; the script aborts if any anchor is missing.
"""
import json
import os
import sys

ROOT = "/data/workspace/geointel"


def replace(path, old, new, label):
    text = open(path, encoding="utf-8").read()
    if old not in text:
        print(f"ANCHOR MISSING [{label}] in {path}")
        sys.exit(1)
    open(path, "w", encoding="utf-8").write(text.replace(old, new, 1))
    print(f"patched [{label}] in {path}")


# ---- 1. sources.json ----
sj = os.path.join(ROOT, "sources.json")
data = json.load(open(sj, encoding="utf-8"))
changed = []
for s in data["sources"]:
    if s["id"] == "wto":
        s["url"] = "https://www.wto.org/library/rss/latest_news_e.xml"
        changed.append("wto")
    if s["id"] == "brookings":
        s["id"] = "cfr"
        s["name"] = "Council on Foreign Relations"
        s["url"] = "https://www.cfr.org/feed"
        changed.append("brookings->cfr")
json.dump(data, open(sj, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("sources.json:", changed, "| total sources:", len(data["sources"]))

# ---- 2. collect.py: HTML guard ----
replace(
    os.path.join(ROOT, "collect.py"),
    """        if data[:2] == b"\\x1f\\x8b":
            import gzip
            data = gzip.decompress(data)
        items = parse_feed(data, source)""",
    """        if data[:2] == b"\\x1f\\x8b":
            import gzip
            data = gzip.decompress(data)
        stripped = data.lstrip()
        if stripped[:15].lower().startswith(b"<!doctype html") or stripped[:6].lower().startswith(b"<html"):
            return source["id"], False, 0, round(time.time() - started, 1), [], \\
                "non-XML response (HTML page - dead or wrong feed URL)"
        items = parse_feed(data, source)""",
    "collect-html-guard",
)

# ---- 3. digest.py: source-health helper + wiring ----
replace(
    os.path.join(ROOT, "digest.py"),
    '''def main():''',
    '''def latest_run_health():
    """读最近一次 collect 运行报告，返回 (ok, total, failed_list)；无报告返回 None。"""
    runs = os.path.join(HERE, "data", "runs")
    try:
        files = sorted(f for f in os.listdir(runs) if f.endswith(".json"))
    except OSError:
        return None
    if not files:
        return None
    try:
        with open(os.path.join(runs, files[-1]), encoding="utf-8") as f:
            rep = json.load(f)
        return rep.get("ok"), rep.get("sources"), rep.get("failed") or []
    except Exception:
        return None


def main():''',
    "digest-health-helper",
)

replace(
    os.path.join(ROOT, "digest.py"),
    '''    header = (
        "---\\n"
        f"date: {day}\\n"
        f"generated_at: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}\\n"
        f"items_selected: {len(items)}\\n"
        f"items_in_window: {len(raw)}\\n"
        f"db_total: {stats['total']}\\n"
        "---\\n\\n"
    )
    open(path, "w", encoding="utf-8").write(header + body.strip() + "\\n")''',
    '''    health = latest_run_health()
    header = (
        "---\\n"
        f"date: {day}\\n"
        f"generated_at: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}\\n"
        f"items_selected: {len(items)}\\n"
        f"items_in_window: {len(raw)}\\n"
        f"db_total: {stats['total']}\\n"
    )
    if health:
        ok, total, failed = health
        header += f"sources_ok: {ok}/{total}\\n"
        if failed:
            header += f"sources_failed: {', '.join(failed)}\\n"
    header += "---\\n\\n"
    body_text = body.strip()
    if health:
        ok, total, failed = health
        line = f"\\n\\n---\\n源状态：{ok}/{total} 正常"
        if failed:
            line += f"，失败：{', '.join(failed)}"
        body_text += line
    open(path, "w", encoding="utf-8").write(header + body_text + "\\n")''',
    "digest-health-wiring",
)

# ---- 4. collect timer: Persistent=true ----
timer = "/data/home/.config/systemd/user/geointel-collect.timer"
replace(
    timer,
    """OnUnitActiveSec=2h
Unit=geointel-collect.service""",
    """OnUnitActiveSec=2h
Persistent=true
Unit=geointel-collect.service""",
    "collect-timer-persistent",
)

print("ALL PATCHES APPLIED")
