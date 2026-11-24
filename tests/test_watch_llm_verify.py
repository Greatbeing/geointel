#!/usr/bin/env python3
"""paid-ready 第 1 步测试：watch.py 的 LLM 语义复核。

Stdlib only，无网络：llm.chat 全程 mock。覆盖：
- 粗筛命中但语义不支持的条目被剔除（日本教育新闻 vs 盟友采纳管制）
- 真支持的条目保留，告警文本附「复核理由 + 原文片段 + 链接」
- LLM 抛异常 / 输出不合规格 → 降级为规则口径，条目保留、标「未复核」，流水线不崩
- parse_verdict 的宽容解析（围栏 JSON / 裸 JSON / 非法输出）
- --llm-verify 与 GEOINTEL_WATCH_LLM_VERIFY 环境变量开关
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import store  # noqa: E402
import watch  # noqa: E402


def _ts(hours_ago):
    """相对 now 的时间戳：固定日期的样本配上 --hours 24 的滑动窗口，
    会在一天中的某些时刻整体滑出窗口，测试变成按钟点flaky。"""
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")


JP_EDU = {
    "id": "noise-jp-edu", "source": "guardian-world", "region": "asia",
    "category": "world",
    "title": "Calls grow for Japan's education minister to step down over scandals",
    "link": "https://example.org/jp-edu",
    "summary": "South Korean minister quits after AI cheating scandal engulfs university exams.",
    "published": _ts(6), "weight": 1.0,
}
ALLY_TRUE = {
    "id": "true-ally", "source": "csis", "region": "global", "category": "security",
    "title": "Netherlands formally adopts export controls aligned with US rules",
    "link": "https://example.org/ally",
    "summary": "The Dutch government implements measures matching US export control policy.",
    "published": _ts(4), "weight": 1.2,
}

UPGRADED_T2 = {
    "id": "t2", "description": "至少两个关键盟友正式采纳与美国等效的管制措施",
    "keywords": ["netherlands", "japan", "adopt", "export control"],
    "keyword_groups": [
        ["netherlands", "japan", "south korea", "singapore"],
        ["adopts", "aligned with us", "implements"],
        ["export control", "export restrictions"],
    ],
    "exclude_keywords": ["education", "exam", "minister quits"],
    "threshold": 1, "type": "count",
    "action": "加速非美供应链合规审计",
    "question": "未来 90 天半导体出口管制升级的概率与应对",
    "decision": "2026-11-23-chips",
}


class TestParseVerdict(unittest.TestCase):
    def test_fenced_json(self):
        out = watch.parse_verdict(
            '```json\n{"verdict": "yes", "reason": "材料确为盟友采纳管制", "quote": "adopts export controls"}\n```')
        self.assertEqual(out, ("yes", "材料确为盟友采纳管制", "adopts export controls"))

    def test_bare_json(self):
        self.assertEqual(watch.parse_verdict('{"verdict": "no", "reason": "r", "quote": ""}'),
                         ("no", "r", ""))

    def test_json_with_prose_around(self):
        self.assertEqual(
            watch.parse_verdict('好的，判断如下：{"verdict": "yes", "reason": "支持", "quote": "q"} 以上。'),
            ("yes", "支持", "q"))

    def test_bad_json_returns_none(self):
        self.assertIsNone(watch.parse_verdict("我觉得不太行"))
        self.assertIsNone(watch.parse_verdict('{"verdict": "maybe"}'))
        self.assertIsNone(watch.parse_verdict('{"verdict": "yes"'))
        self.assertIsNone(watch.parse_verdict(""))
        self.assertIsNone(watch.parse_verdict("[1, 2, 3]"))

    def test_verdict_case_insensitive_and_trimmed(self):
        self.assertEqual(watch.parse_verdict('{"verdict": " Yes ", "reason": "r"}'),
                         ("yes", "r", ""))


class TestVerifyHit(unittest.TestCase):
    def _t(self):
        return dict(UPGRADED_T2)

    def test_yes_keeps_with_reason_and_quote(self):
        reply = json.dumps({"verdict": "yes", "reason": "荷兰政府正式采纳管制",
                            "quote": "Netherlands formally adopts export controls"})
        with mock.patch.object(watch.llm, "chat", return_value=reply) as chat:
            v = watch.verify_hit(self._t(), ALLY_TRUE)
        self.assertTrue(v["ok"])
        self.assertEqual(v["verdict"], "yes")
        self.assertIn("荷兰", v["reason"])
        chat.assert_called_once()
        # 提示词里要有材料标题/摘要与触发条件描述，供判断
        prompt = chat.call_args[0][0]
        self.assertIn("Netherlands formally adopts export controls", prompt)
        self.assertIn("盟友", prompt)

    def test_no_drops(self):
        reply = json.dumps({"verdict": "no", "reason": "是教育部丑闻", "quote": "education minister"})
        with mock.patch.object(watch.llm, "chat", return_value=reply):
            v = watch.verify_hit(self._t(), JP_EDU)
        self.assertTrue(v["ok"])
        self.assertEqual(v["verdict"], "no")

    def test_llm_exception_degrades(self):
        with mock.patch.object(watch.llm, "chat",
                               side_effect=RuntimeError("all models failed")):
            v = watch.verify_hit(self._t(), ALLY_TRUE)
        self.assertFalse(v["ok"])
        self.assertIn("不可用", v["reason"])

    def test_bad_output_degrades(self):
        with mock.patch.object(watch.llm, "chat", return_value="模型跑题了"):
            v = watch.verify_hit(self._t(), ALLY_TRUE)
        self.assertFalse(v["ok"])


class TestVerifyHits(unittest.TestCase):
    def test_mixed_hits_only_yes_kept(self):
        def fake_chat(prompt, system=None, timeout=90, max_tokens=2000):
            if "education minister" in prompt:
                return json.dumps({"verdict": "no", "reason": "教育新闻", "quote": "education"})
            return json.dumps({"verdict": "yes", "reason": "真采纳", "quote": "adopts"})

        with mock.patch.object(watch.llm, "chat", side_effect=fake_chat):
            kept, meta = watch.verify_hits(dict(UPGRADED_T2), [ALLY_TRUE, JP_EDU])
        self.assertEqual([it["id"] for it in kept], ["true-ally"])
        self.assertEqual(meta["noise-jp-edu"]["verdict"], "no")
        self.assertEqual(meta["true-ally"]["verdict"], "yes")

    def test_single_failure_kept_as_unreviewed(self):
        # 单条复核失败：该条按规则口径保留，不能静默吞掉
        def fake_chat(prompt, system=None, timeout=90, max_tokens=2000):
            if "education minister" in prompt:
                raise RuntimeError("gateway down")
            return json.dumps({"verdict": "yes", "reason": "真采纳", "quote": "adopts"})

        with mock.patch.object(watch.llm, "chat", side_effect=fake_chat):
            kept, meta = watch.verify_hits(dict(UPGRADED_T2), [JP_EDU, ALLY_TRUE])
        ids = {it["id"] for it in kept}
        self.assertEqual(ids, {"noise-jp-edu", "true-ally"})
        self.assertFalse(meta["noise-jp-edu"]["ok"])
        self.assertTrue(meta["true-ally"]["ok"])


class TestWatchMainWithVerify(unittest.TestCase):
    MEMO = {
        "question": "未来 90 天半导体出口管制升级的概率与应对",
        "scenarios": [{"name": "A", "prob": 60}, {"name": "B", "prob": 40}],
        "triggers": [UPGRADED_T2],
        "confidence": "medium", "review_date": "2027-02-18",
    }

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old_db = store.DB
        store.DB = os.path.join(self.tmp.name, "test.db")
        store.upsert_many([JP_EDU, ALLY_TRUE])
        self.dec = os.path.join(self.tmp.name, "dec")
        os.makedirs(self.dec)
        with open(os.path.join(self.dec, "2026-11-23-chips.json"), "w",
                  encoding="utf-8") as f:
            json.dump(self.MEMO, f, ensure_ascii=False)
        self.out = os.path.join(self.tmp.name, "alerts")

    def tearDown(self):
        store.DB = self._old_db
        self.tmp.cleanup()

    def _run(self, extra=None):
        argv = ["--hours", "24", "--decisions-dir", self.dec, "--out-dir", self.out]
        if extra:
            argv += extra
        with mock.patch.object(watch.render, "build", return_value={}):
            rc = watch.main(argv)
        self.assertEqual(rc, 0)
        # 输出文件按运行当天日期命名，不能写死日期
        mds = sorted(n for n in os.listdir(self.out) if n.endswith(".md"))
        self.assertEqual(len(mds), 1, mds)
        with open(os.path.join(self.out, mds[0]), encoding="utf-8") as f:
            return f.read()

    def test_verify_removes_false_positive_and_keeps_reason(self):
        def fake_chat(prompt, system=None, timeout=90, max_tokens=2000):
            if "education minister" in prompt:
                return json.dumps({"verdict": "no",
                                   "reason": "日本教育部丑闻，与出口管制无关",
                                   "quote": "education minister"})
            return json.dumps({"verdict": "yes", "reason": "荷兰正式采纳对美等效管制",
                               "quote": "Netherlands formally adopts export controls"})

        with mock.patch.object(watch.llm, "chat", side_effect=fake_chat):
            text = self._run(["--llm-verify"])
        self.assertIn("[t2]", text)
        self.assertNotIn("Japan's education minister", text)   # 误报被剔除
        self.assertIn("LLM 复核确认 1 条", text)
        self.assertIn("复核：荷兰正式采纳对美等效管制", text)      # 理由入告警
        self.assertIn("原文：Netherlands formally adopts export controls", text)
        self.assertIn("https://example.org/ally", text)          # 原文链接

    def test_llm_down_degrades_to_rules_and_pipeline_survives(self):
        with mock.patch.object(watch.llm, "chat",
                               side_effect=RuntimeError("all models failed")):
            text = self._run(["--llm-verify"])
        # 降级：两条粗筛命中都在（exclude_keywords 仍生效，教育新闻本来就被排除词挡住）
        self.assertIn("[t2]", text)
        self.assertIn("未复核", text)
        self.assertIn("Netherlands formally adopts", text)

    def test_env_var_enables_verify(self):
        def fake_chat(prompt, system=None, timeout=90, max_tokens=2000):
            return json.dumps({"verdict": "yes", "reason": "支持", "quote": "q"})

        old = os.environ.pop("GEOINTEL_WATCH_LLM_VERIFY", None)
        try:
            os.environ["GEOINTEL_WATCH_LLM_VERIFY"] = "1"
            with mock.patch.object(watch.llm, "chat", side_effect=fake_chat) as chat:
                self._run()
            self.assertTrue(chat.called)  # 未传 --llm-verify 也被环境变量打开
        finally:
            if old is not None:
                os.environ["GEOINTEL_WATCH_LLM_VERIFY"] = old
            else:
                os.environ.pop("GEOINTEL_WATCH_LLM_VERIFY", None)

    def test_default_off(self):
        with mock.patch.object(watch.llm, "chat", return_value="") as chat:
            self._run()
        self.assertFalse(chat.called)  # 默认关闭，零成本

    def test_env_flag_parsing(self):
        with mock.patch.dict(os.environ, {"GEOINTEL_WATCH_LLM_VERIFY": "TRUE"}):
            self.assertTrue(watch._env_flag("GEOINTEL_WATCH_LLM_VERIFY"))
        with mock.patch.dict(os.environ, {"GEOINTEL_WATCH_LLM_VERIFY": "0"}):
            self.assertFalse(watch._env_flag("GEOINTEL_WATCH_LLM_VERIFY"))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GEOINTEL_WATCH_LLM_VERIFY", None)
            self.assertFalse(watch._env_flag("GEOINTEL_WATCH_LLM_VERIFY"))


if __name__ == "__main__":
    unittest.main()
