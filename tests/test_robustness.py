#!/usr/bin/env python3
"""paid-ready 第 4 步测试：稳健性。

覆盖：
- llm.fallback_config：环境变量三件套的启用/禁用（key 可缺省）
- llm.chat：主网关成功不打备用；主网关挂了自动回退备用端点并带
  Authorization: Bearer <key>；备用也挂才抛错且错误信息含两边原因
- 未配置备用时行为与旧版一致（主网关失败直接抛原错误）
- collect：失败源汇总日志（名字 + 原因出现在输出里）
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import collect  # noqa: E402
import llm  # noqa: E402
import store  # noqa: E402


def _isolate_llm_env(tc):
    """清掉备用端点环境变量，测试结束自动恢复，避免本机配置干扰。"""
    p = mock.patch.dict(os.environ, {})
    p.start()
    tc.addCleanup(p.stop)
    for k in llm.FALLBACK_ENV.values():
        os.environ.pop(k, None)


def _reply(text="ok"):
    return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}],
            "usage": {"completion_tokens": 5}}


class TestFallbackConfig(unittest.TestCase):
    def _env(self, **kw):
        return mock.patch.dict(os.environ, kw, clear=False)

    def test_enabled_when_base_and_model_set(self):
        with self._env(GEOINTEL_LLM_BASE_URL="https://api.example.com/v1/",
                       GEOINTEL_LLM_MODEL="deepseek-chat",
                       GEOINTEL_LLM_API_KEY="sk-test"):
            cfg = llm.fallback_config()
        self.assertEqual(cfg, {"base_url": "https://api.example.com/v1",
                               "model": "deepseek-chat", "api_key": "sk-test"})

    def test_key_optional(self):
        with self._env(GEOINTEL_LLM_BASE_URL="https://api.example.com/v1",
                       GEOINTEL_LLM_MODEL="m"):
            cfg = llm.fallback_config()
        self.assertEqual(cfg["api_key"], "")

    def test_disabled_without_base_or_model(self):
        with self._env(GEOINTEL_LLM_BASE_URL="", GEOINTEL_LLM_MODEL=""):
            self.assertIsNone(llm.fallback_config())
        with self._env(GEOINTEL_LLM_BASE_URL="https://x/v1", GEOINTEL_LLM_MODEL=""):
            self.assertIsNone(llm.fallback_config())
        with self._env(GEOINTEL_LLM_BASE_URL="", GEOINTEL_LLM_MODEL="m"):
            self.assertIsNone(llm.fallback_config())


class TestChatFallback(unittest.TestCase):
    def setUp(self):
        _isolate_llm_env(self)

    def test_local_success_no_fallback(self):
        calls = []

        def fake_local(model, messages, max_tokens, timeout):
            calls.append(("local", model))
            return "local answer"

        with mock.patch.object(llm, "_chat_local", side_effect=fake_local), \
             mock.patch.object(llm, "_chat_fallback") as fb:
            out = llm.chat("hi")
        self.assertEqual(out, "local answer")
        self.assertEqual(calls, [("local", None)])
        self.assertFalse(fb.called)

    def test_local_failure_falls_back(self):
        sent = {}

        def fake_fallback(cfg, messages, max_tokens, timeout):
            sent.update(cfg, messages=messages)
            return "fallback answer"

        with mock.patch.object(llm, "_chat_local",
                               side_effect=RuntimeError("all models failed")), \
             mock.patch.object(llm, "_chat_fallback", side_effect=fake_fallback), \
             mock.patch.dict(os.environ, {
                 "GEOINTEL_LLM_BASE_URL": "https://api.example.com/v1",
                 "GEOINTEL_LLM_MODEL": "deepseek-chat",
                 "GEOINTEL_LLM_API_KEY": "sk-1"}):
            out = llm.chat("hi", system="sys")
        self.assertEqual(out, "fallback answer")
        self.assertEqual(sent["base_url"], "https://api.example.com/v1")
        self.assertEqual(sent["model"], "deepseek-chat")
        self.assertEqual(sent["api_key"], "sk-1")
        # messages 结构完整（system + user）
        self.assertEqual(sent["messages"],
                         [{"role": "system", "content": "sys"},
                          {"role": "user", "content": "hi"}])

    def test_no_fallback_config_raises_local_error(self):
        with mock.patch.object(llm, "_chat_local",
                               side_effect=RuntimeError("all models failed")):
            with self.assertRaises(RuntimeError) as ctx:
                llm.chat("hi")
        # 未配置备用端点：直接抛主网关原错误，不带 fallback 段
        self.assertIn("all models failed", str(ctx.exception))
        self.assertNotIn("fallback", str(ctx.exception))

    def test_both_fail_reports_both(self):
        with mock.patch.object(llm, "_chat_local",
                               side_effect=RuntimeError("local dead")), \
             mock.patch.object(llm, "_chat_fallback",
                               side_effect=RuntimeError("fb dead")), \
             mock.patch.dict(os.environ, {
                 "GEOINTEL_LLM_BASE_URL": "https://api.example.com/v1",
                 "GEOINTEL_LLM_MODEL": "m"}):
            with self.assertRaises(RuntimeError) as ctx:
                llm.chat("hi")
        self.assertIn("local dead", str(ctx.exception))
        self.assertIn("fb dead", str(ctx.exception))

    def test_fallback_once_posts_to_base_url_with_key(self):
        # _chat_fallback 经 _once 走 _post：验证 URL、头与 payload
        captured = {}

        def fake_post(url, headers, payload, timeout):
            captured.update(url=url, headers=headers, payload=payload)
            return ("fb text", "stop", 5, {})

        with mock.patch.object(llm, "_post", side_effect=fake_post):
            out = llm._chat_fallback(
                {"base_url": "https://api.example.com/v1", "model": "deepseek-chat",
                 "api_key": "sk-2"},
                [{"role": "user", "content": "hi"}], 4000, 60)
        self.assertEqual(out, "fb text")
        self.assertEqual(captured["url"], "https://api.example.com/v1/chat/completions")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer sk-2")
        self.assertEqual(captured["payload"]["model"], "deepseek-chat")

    def test_fallback_without_key_uses_bearer_local(self):
        captured = {}

        def fake_post(url, headers, payload, timeout):
            captured.update(headers=headers)
            return ("t", "stop", 1, {})

        with mock.patch.object(llm, "_post", side_effect=fake_post):
            llm._chat_fallback(
                {"base_url": "https://api.example.com/v1", "model": "m", "api_key": ""},
                [{"role": "user", "content": "hi"}], 4000, 60)
        self.assertEqual(captured["headers"]["Authorization"], "Bearer local")

    def test_fallback_empty_content_retries_with_doubled_budget(self):
        budgets = []

        def fake_post(url, headers, payload, timeout):
            budgets.append(payload["max_tokens"])
            if len(budgets) == 1:
                return ("", "length", 4000, {"reasoning_tokens": 4000})
            return ("real text", "stop", 5, {})

        with mock.patch.object(llm, "_post", side_effect=fake_post):
            out = llm._chat_fallback(
                {"base_url": "https://x/v1", "model": "m", "api_key": ""},
                [{"role": "user", "content": "hi"}], 8000, 60)
        self.assertEqual(out, "real text")
        self.assertEqual(budgets, [8000, 16000])


_SRC_GOOD = {"id": "good", "name": "Good", "url": "https://example.org/good",
             "region": "global", "category": "tech", "weight": 1.0}
_SRC_BAD = {"id": "bad-feed", "name": "Bad", "url": "https://example.org/bad",
            "region": "global", "category": "tech", "weight": 1.0}
_ITEM = {"id": "x1", "source": "good", "region": "global", "category": "tech",
         "title": "t", "link": "https://l", "summary": "s",
         "published": "2026-11-23T00:00:00Z", "weight": 1.0}


def _fake_fetch(source, timeout):
    """替身 collect.fetch：id 为 good 的源成功，其余一律 404。"""
    if source["id"] == "good":
        return source["id"], True, 1, 0.3, [dict(_ITEM)], ""
    return source["id"], False, 0, 0.5, [], "HTTPError: HTTP Error 404: Not Found"


class TestCollectFailureSummary(unittest.TestCase):
    """collect.main 的失败源汇总日志与退出码；网络与入库全部 mock。"""

    def _run(self, sources):
        """在临时目录跑 collect.main，返回 (退出码, stdout)。

        HERE 指到临时目录 → data/runs/ 写在临时区，不污染仓库；
        upsert_many 被替换，不碰真实库。
        """
        with tempfile.TemporaryDirectory() as d:
            sources_path = os.path.join(d, "sources.json")
            with open(sources_path, "w", encoding="utf-8") as f:
                json.dump({"sources": sources}, f)
            buf = io.StringIO()
            with mock.patch.object(collect, "HERE", d), \
                 mock.patch.object(collect, "fetch", side_effect=_fake_fetch), \
                 mock.patch.object(store, "upsert_many", return_value=1), \
                 contextlib.redirect_stdout(buf):
                rc = collect.main(["--sources", sources_path])
        return rc, buf.getvalue()

    def _summary_lines(self, out, header):
        """汇总块的非空行（标题行之后、done: 之前）。"""
        self.assertIn(header, out)
        block = out.split(header + "\n", 1)[1].split("done:", 1)[0]
        return [ln for ln in block.splitlines() if ln.strip()]

    def test_partial_failure_summarized_exit_zero(self):
        rc, out = self._run([_SRC_GOOD, _SRC_BAD])
        lines = self._summary_lines(out, "collect failed sources (1/2):")
        self.assertEqual(len(lines), 1)  # 只列失败源
        self.assertIn("bad-feed", lines[0])
        self.assertIn("404", lines[0])  # 带失败原因
        self.assertIn("done: 1/2 sources ok", out)
        # 既有语义：有源成功就返回 0（源一多总有几个挂，部分失败不应中断
        # 流水线）；全部失败才返回 1
        self.assertEqual(rc, 0)

    def test_all_failed_exit_one(self):
        bad2 = dict(_SRC_BAD, id="bad-two", url="https://example.org/bad2")
        rc, out = self._run([_SRC_BAD, bad2])
        lines = self._summary_lines(out, "collect failed sources (2/2):")
        self.assertEqual(len(lines), 2)
        self.assertIn("bad-feed", lines[0])  # 按源名排序
        self.assertIn("bad-two", lines[1])
        self.assertIn("done: 0/2 sources ok", out)
        self.assertEqual(rc, 1)

    def test_no_failure_no_summary_block(self):
        rc, out = self._run([_SRC_GOOD])
        self.assertNotIn("collect failed sources", out)
        self.assertIn("done: 1/1 sources ok", out)
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
