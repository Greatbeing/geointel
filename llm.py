#!/usr/bin/env python3
"""llm.py — 统一走本机 magpie 网关，带模型降级与「推理吃光 token」重试。

背景：免费池里多数是推理模型（LongCat、Nemotron、Mistral），会先写很长的
reasoning_content，再写正文。max_tokens 给小了，正文就是空的（finish_reason=length）。
所以这里：正文为空且因长度截断 → 同一模型把预算翻倍重试；仍失败 → 换下一个模型。
"""
import json
import urllib.error
import urllib.request

GATEWAY = "http://127.0.0.1:3425"
MODELS = [
    "longcat/LongCat-2.5",
    "kilo/dots-studio/dots-3-note-preview:free",
    "nvidia/nvidia/nemotron-3-ultra-550b-a55b",
    "group/daily",
]
DEFAULT_TIMEOUT = 420
MAX_TOKENS_CAP = 32000


def _once(model, messages, max_tokens, timeout):
    body = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }).encode("utf-8")
    req = urllib.request.Request(
        GATEWAY + "/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer local"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    usage = data.get("usage") or {}
    return ((msg.get("content") or "").strip(), choice.get("finish_reason"),
            usage.get("completion_tokens"), usage.get("completion_tokens_details") or {})


def chat(prompt, system=None, model=None, timeout=DEFAULT_TIMEOUT, max_tokens=16000):
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    errors = []
    for m in ([model] if model else MODELS):
        budget = max_tokens
        for attempt in range(2):
            try:
                text, finish, used, detail = _once(m, messages, budget, timeout)
            except Exception as e:
                extra = ""
                if isinstance(e, urllib.error.HTTPError):
                    try:
                        extra = e.read(240).decode("utf-8", "replace")
                    except Exception:
                        pass
                errors.append(f"{m}@{budget}: {type(e).__name__} {extra[:160]}")
                break
            if text:
                return text
            reasoning = detail.get("reasoning_tokens")
            errors.append(f"{m}@{budget}: empty finish={finish} used={used} reasoning={reasoning}")
            if finish == "length" and budget < MAX_TOKENS_CAP:
                budget = min(budget * 2, MAX_TOKENS_CAP)
                continue
            break
    raise RuntimeError("all models failed; " + " | ".join(errors[-6:]))
