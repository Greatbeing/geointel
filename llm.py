#!/usr/bin/env python3
"""llm.py — 统一走本机 magpie 网关，带模型降级。

不直连任何厂商；group/daily 内部已经是 order 路由（LongCat 首选），
外面再包一层 group 级降级，防止整组挂死。
"""
import json
import urllib.error
import urllib.request

GATEWAY = "http://127.0.0.1:3425"
MODELS = ["group/daily", "group/fast", "longcat/LongCat-2.5"]
DEFAULT_TIMEOUT = 300


def chat(prompt, system=None, model=None, timeout=DEFAULT_TIMEOUT, max_tokens=4000):
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    last_err = None
    for m in ([model] if model else MODELS):
        body = json.dumps({
            "model": m,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }).encode("utf-8")
        req = urllib.request.Request(
            GATEWAY + "/v1/chat/completions", data=body,
            headers={"Content-Type": "application/json", "Authorization": "Bearer local"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            text = (msg.get("content") or "").strip()
            if text:
                return text
            last_err = f"{m}: empty content"
        except Exception as e:
            detail = ""
            if isinstance(e, urllib.error.HTTPError):
                try:
                    detail = e.read(300).decode("utf-8", "replace")
                except Exception:
                    pass
            last_err = f"{m}: {type(e).__name__} {e} {detail[:200]}"
    raise RuntimeError(f"all models failed; last={last_err}")
