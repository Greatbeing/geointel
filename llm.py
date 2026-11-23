#!/usr/bin/env python3
"""llm.py — 统一走本机 magpie 网关，带模型降级与「推理吃光 token」重试。

背景：免费池里多数是推理模型（LongCat、Nemotron、Mistral），会先写很长的
reasoning_content，再写正文。max_tokens 给小了，正文就是空的（finish_reason=length）。
所以这里：正文为空且因长度截断 → 同一模型把预算翻倍重试；仍失败 → 换下一个模型。

稳健性（paid-ready）：主网关整体失败（所有模型都试完仍不行）时，自动回退到
环境变量配置的备用 OpenAI 兼容端点：
  GEOINTEL_LLM_BASE_URL   例：https://api.deepseek.com/v1
  GEOINTEL_LLM_API_KEY    备用端点的 key（不设则不带 Authorization）
  GEOINTEL_LLM_MODEL      备用端点的模型名，如 deepseek-chat
三个变量都不设 → 行为与旧版完全一致，零影响。备用端点也失败才抛
RuntimeError（附两边的失败原因）。
"""
import json
import os
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
MAX_TOKENS_CAP = 64000

FALLBACK_ENV = {
    "base_url": "GEOINTEL_LLM_BASE_URL",
    "api_key": "GEOINTEL_LLM_API_KEY",
    "model": "GEOINTEL_LLM_MODEL",
}


def fallback_config():
    """读备用端点配置；BASE_URL 与 MODEL 都齐了才算启用（key 可为空）。"""
    base = (os.environ.get(FALLBACK_ENV["base_url"]) or "").strip().rstrip("/")
    model = (os.environ.get(FALLBACK_ENV["model"]) or "").strip()
    key = (os.environ.get(FALLBACK_ENV["api_key"]) or "").strip()
    if not base or not model:
        return None
    return {"base_url": base, "model": model, "api_key": key}


def _post(url, headers, payload, timeout):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    usage = data.get("usage") or {}
    return ((msg.get("content") or "").strip(), choice.get("finish_reason"),
            usage.get("completion_tokens"),
            usage.get("completion_tokens_details") or {})


def _once(model, messages, max_tokens, timeout, base_url=None, api_key=None):
    base = base_url or GATEWAY
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    else:
        headers["Authorization"] = "Bearer local"
    return _post(base + "/chat/completions", headers,
                 {"model": model, "messages": messages, "max_tokens": max_tokens,
                  "temperature": 0.3}, timeout)


def _chat_local(model, messages, max_tokens, timeout):
    """原有逻辑：本地网关上的多模型轮转 + 预算翻倍重试。"""
    errors = []
    for m in ([model] if model else MODELS):
        budget = max_tokens
        for _attempt in range(2):
            try:
                text, finish, used, detail = _once(m, messages, budget, timeout)
            except Exception as e:  # noqa: BLE001 - 记录后换下一个模型
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
    raise RuntimeError("local gateway models exhausted; " + " | ".join(errors[-6:]))


def _chat_fallback(cfg, messages, max_tokens, timeout):
    """备用 OpenAI 兼容端点：单模型，带一次预算翻倍重试。"""
    errors = []
    budget = max_tokens
    for _attempt in range(2):
        try:
            text, finish, used, detail = _once(
                cfg["model"], messages, budget, timeout,
                base_url=cfg["base_url"], api_key=cfg["api_key"])
        except Exception as e:  # noqa: BLE001 — 记录后按重试/放弃处理
            extra = ""
            if isinstance(e, urllib.error.HTTPError):
                try:
                    extra = e.read(240).decode("utf-8", "replace")
                except Exception:
                    pass
            errors.append(f"{cfg['model']}@{budget}: {type(e).__name__} {extra[:160]}")
            raise RuntimeError("fallback endpoint failed; " + " | ".join(errors)) from e
        if text:
            return text
        reasoning = (detail or {}).get("reasoning_tokens")
        errors.append(f"{cfg['model']}@{budget}: empty finish={finish} used={used} reasoning={reasoning}")
        if finish == "length" and budget < MAX_TOKENS_CAP:
            budget = min(budget * 2, MAX_TOKENS_CAP)
            continue
        raise RuntimeError("fallback endpoint returned empty; " + " | ".join(errors))
    raise RuntimeError("fallback endpoint failed; " + " | ".join(errors))


def chat(prompt, system=None, model=None, timeout=DEFAULT_TIMEOUT, max_tokens=16000):
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    local_err = None
    try:
        return _chat_local(model, messages, max_tokens, timeout)
    except Exception as e:  # noqa: BLE001 — 主网关挂了，试备用端点
        local_err = e

    cfg = fallback_config()
    if cfg is None:
        raise local_err
    try:
        return _chat_fallback(cfg, messages, max_tokens, timeout)
    except Exception as fb_err:  # noqa: BLE001
        raise RuntimeError(
            "all endpoints failed; local: %s | fallback: %s" % (local_err, fb_err))
