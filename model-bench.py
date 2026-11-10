#!/usr/bin/env python3
"""Benchmark candidate models for geointel digest: speed + Chinese output quality.
Uses llm._once directly against the local Magpie gateway (127.0.0.1:3425).
Prompt: 15 real items from the DB, ask for a short Chinese briefing (digest-style).
"""
import sys
import time

sys.path.insert(0, "/data/workspace/geointel")
import llm
import store

MODELS = [
    "group/fast",
    "auto/glm-5.3-flash",
    "auto/deepseek-v4.1-flash",
    "auto/nemotron-3.5-lightning",
    "longcat/LongCat-2.5",
]

items = store.recent(hours=24, limit=600)[:15]
material = "\n".join(
    f"[{i}] ({it['source']}) {it['title']} — {(it.get('summary') or '')[:150]}"
    for i, it in enumerate(items, 1)
)
prompt = (
    "下面是过去24小时抓取到的15条材料（来源、标题、摘要）。\n"
    "请用中文写一份简短情报简报，结构：## 今日要点（3条，每条一句话，句末用来源标注）\n"
    "## 值得跟踪的信号（2条：现象→为什么值得跟踪→下一步看什么）。只使用材料里的事实。\n\n材料：\n" + material
)
system = "你是一名严谨的情报分析师。你只根据给定材料写作，绝不补充材料之外的事实。"

for model in MODELS:
    t0 = time.time()
    try:
        content, finish, tokens, details = llm._once(
            model,
            [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            max_tokens=4000,
            timeout=240,
        )
        dt = time.time() - t0
        print(f"=== {model} ===")
        print(f"  time: {dt:.1f}s  finish: {finish}  tokens: {tokens}")
        print(f"  chars: {len(content)}  preview: {content[:200].replace(chr(10), ' ')}")
    except Exception as e:
        dt = time.time() - t0
        print(f"=== {model} ===")
        print(f"  FAILED after {dt:.1f}s: {type(e).__name__}: {e}")
    print(flush=True)
print("BENCHMARK DONE")
