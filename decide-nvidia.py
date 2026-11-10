#!/usr/bin/env python3
"""Patch decide.py to use NVIDIA Nemotron for decision analysis (user 2026-11-11:
'inference/decision model uses NVIDIA-provided models, quota is ample').
- Add DECIDE_MODEL = nvidia/nvidia/nemotron-3-ultra-550b-a55b (NVIDIA NIM via Magpie).
- Pass model=DECIDE_MODEL to llm.chat so decide uses NVIDIA only (digest keeps group/fast).
- Raise max_tokens to 32000 (the llm.py cap) to give the reasoning model room for
  both reasoning_content and the full memo output.
"""
import os

ROOT = "/data/workspace/geointel"
p = os.path.join(ROOT, "decide.py")
text = open(p, encoding="utf-8").read()

# 1. Add DECIDE_MODEL constant after the SYSTEM definition (find the TEMPLATE start).
anchor = 'TEMPLATE = """'
assert anchor in text, "TEMPLATE anchor missing"
const = (
    '# Decision model: NVIDIA Nemotron 3 Ultra 550B via Magpie (user 2026-11-11:\n'
    '# NVIDIA-provided models, quota ample). Digest keeps llm.MODELS (group/fast).\n'
    'DECIDE_MODEL = "nvidia/nvidia/nemotron-3-ultra-550b-a55b"\n\n'
)
if "DECIDE_MODEL" not in text:
    text = text.replace(anchor, const + anchor, 1)

# 2. Use DECIDE_MODEL + larger budget in the llm.chat call.
old_call = "body = llm.chat(prompt, system=SYSTEM, timeout=480, max_tokens=16000)"
new_call = "body = llm.chat(prompt, system=SYSTEM, model=DECIDE_MODEL, timeout=480, max_tokens=32000)"
assert old_call in text, "llm.chat call anchor missing"
text = text.replace(old_call, new_call, 1)

open(p, "w", encoding="utf-8").write(text)
print("decide.py patched: DECIDE_MODEL=NVIDIA Nemotron, max_tokens=32000")
