#!/usr/bin/env python3
"""memo.py — 解析 geointel 决策 memo（.md + 同名 .json），供 deliver.py / score.py 共用。

只读解析，不调 LLM、不写文件。memo 结构：
  front-matter：question / generated_at / horizon_days / items_used
  ```json 代码块：scenarios / triggers / confidence / review_date
  正文：## 1. 问题与边界 ... ## 8. 复盘日期
同名 .json 文件（若存在）与代码块内容一致，优先用它取结构化数据。
"""
import json
import os
import re

FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)
DATE_PREFIX_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def memo_date_from_name(path):
    """从文件名取 memo 日期（YYYY-MM-DD 前缀）；取不到返回 None。"""
    m = DATE_PREFIX_RE.search(os.path.basename(path))
    return m.group(1) if m else None


def _parse_front_matter(text):
    m = FRONT_MATTER_RE.search(text)
    fm = {}
    if not m:
        return fm
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm


def load_memo(path):
    """读一个 memo，返回 dict：stem / path / memo_date / frontmatter / data / body。"""
    path = os.path.abspath(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    with open(path, encoding="utf-8") as f:
        text = f.read()
    fm = _parse_front_matter(text)
    body = FRONT_MATTER_RE.sub("", text, count=1)
    data = {}
    json_path = os.path.splitext(path)[0] + ".json"
    if os.path.exists(json_path):
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
    else:
        m = JSON_BLOCK_RE.search(text)
        if m:
            data = json.loads(m.group(1))
    memo_date = memo_date_from_name(path)
    if not memo_date:
        memo_date = (fm.get("generated_at", "")[:10] or None)
    return {
        "stem": stem,
        "path": path,
        "memo_date": memo_date,
        "frontmatter": fm,
        "data": data,
        "body": body,
    }


def latest_memo(decisions_dir):
    """按文件名日期取最新 memo 的路径；没有则返回 None。"""
    if not os.path.isdir(decisions_dir):
        return None
    cands = [
        os.path.join(decisions_dir, n)
        for n in os.listdir(decisions_dir)
        if n.endswith(".md")
    ]
    dated = [(memo_date_from_name(p) or "", p) for p in cands]
    dated = [(d, p) for d, p in dated if d]
    if not dated:
        return None
    dated.sort()
    return dated[-1][1]
