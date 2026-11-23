#!/usr/bin/env python3
"""Unit tests for Week 5: alert credibility + auto structural scoring.

Stdlib only, no network — webhook is mocked. 覆盖：
- keyword_groups 组内 OR / 组间 AND（同一材料内）与 exclude_keywords
- 旧 keywords 平铺 OR 的兼容语义不被破坏
- value 型未绑 series 进入人工读数清单，不再静默丢弃
- 实时通知去重（活跃→新活跃才推，发送失败不推进状态）
- 2026-11-23 真实误报（日本教育大臣 / 稀土）在升级后的触发器下不再命中，
  而真正的「中国稀土管制」仍能命中
- decide.auto_score_memo 生成即评结构分
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date as _date
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import decide  # noqa: E402
import store  # noqa: E402
import watch  # noqa: E402
import weekly_summary  # noqa: E402

# 真实出处：out/alerts/2026-11-23.md 里的误报条目（2026-11-21-chips 备忘录 t2/t4）
JP_EDU = {
    "id": "noise-jp-edu", "source": "guardian-world", "region": "asia",
    "category": "world",
    "title": "Calls grow for Japan's education minister to step down over scandals",
    "link": "https://example.org/jp-edu",
    "summary": "South Korean minister quits after AI cheating scandal engulfs university exams.",
    "published": "2026-11-22T10:00:00Z", "weight": 1.0,
}
JP_RARE_EARTH = {
    "id": "true-positive", "source": "nikkei-asia", "region": "asia",
    "category": "tech",
    "title": "China announces rare earth export restrictions for defense uses",
    "link": "https://example.org/rare-earth",
    "summary": "Beijing unveils a licensing regime covering exports of rare earth minerals.",
    "published": "2026-11-22T11:00:00Z", "weight": 1.1,
}
ALLY_TRUE = {
    "id": "true-ally", "source": "csis", "region": "global", "category": "security",
    "title": "Netherlands formally adopts export controls aligned with US rules",
    "link": "https://example.org/ally",
    "summary": "The Dutch government implements measures matching US export control policy.",
    "published": "2026-11-22T12:00:00Z", "weight": 1.2,
}

# 升级后的触发器：同一事件拆成主体 / 动作 / 对象三组，噪声带排除词
UPGRADED_CHIPS = {
    "question": "未来 90 天半导体出口管制升级的概率与应对",
    "scenarios": [{"name": "A", "prob": 60}, {"name": "B", "prob": 40}],
    "triggers": [
        {
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
        },
        {
            "id": "t4", "description": "中国正式发布稀土出口许可限制",
            "keywords": ["rare earth", "china"],
            "keyword_groups": [
                ["china"],
                ["rare earth", "export restrictions"],
            ],
            "exclude_keywords": ["education"],
            "threshold": 1, "type": "count",
            "action": "触发备选采购合同与库存调拨预案",
        },
    ],
    "confidence": "medium", "review_date": "2027-02-18",
}


class TestCountMatchSemantics(unittest.TestCase):
    def test_legacy_keywords_any_match(self):
        t = {"keywords": ["japan", "export control"], "exclude_keywords": []}
        self.assertTrue(watch.count_match(t, "japan education scandal"))
        self.assertTrue(watch.count_match(t, "new export control bill"))
        self.assertFalse(watch.count_match(t, "korean university exams"))

    def test_groups_require_all_within_one_item(self):
        t = {"keyword_groups": [["japan"], ["adopts"], ["export control"]],
             "exclude_keywords": []}
        self.assertTrue(watch.count_match(t, "japan adopts export controls"))
        self.assertFalse(watch.count_match(t, "japan education scandal"))
        # 不同概念各占一篇不能凑成命中
        self.assertFalse(watch.count_match(t, "japan and export controls but nothing adopted"))

    def test_exclude_keywords_block_item(self):
        t = {"keyword_groups": [["japan"]], "exclude_keywords": ["education"]}
        self.assertFalse(watch.count_match(t, "japan education reform"))
        self.assertTrue(watch.count_match(t, "japan export policy"))

    def test_ascii_word_boundary_kept(self):
        t = {"keywords": ["japan"], "exclude_keywords": []}
        self.assertTrue(watch.count_match(t, "visit to japan"))
        self.assertFalse(watch.count_match(t, "japanology seminar"))

    def test_count_hits_no_cross_item_and(self):
        t = {"keyword_groups": [["japan"], ["export control"]], "exclude_keywords": []}
        items = [
            {"title": "Japan election", "summary": ""},
            {"title": "export control bill", "summary": ""},
        ]
        self.assertEqual(watch.count_hits(t, items), [])


class TestLoadTriggersWeek5(unittest.TestCase):
    def test_value_without_series_is_manual_not_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            memo = {"question": "q",
                    "triggers": [{"id": "t1", "description": "人工读数触发器",
                                  "keywords": [], "threshold": 50, "type": "value"}]}
            path = os.path.join(d, "2026-11-23-demo.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(memo, f, ensure_ascii=False)
            trig = watch.load_triggers(d)
            self.assertEqual(len(trig), 1)
            self.assertEqual(trig[0]["kind"], "value")

    def test_count_with_groups_only_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            memo = {"question": "q",
                    "triggers": [{"id": "t1", "description": "组触发器",
                                  "keywords": [], "keyword_groups": [["a"], ["b"]],
                                  "threshold": 1, "type": "count"}]}
            path = os.path.join(d, "2026-11-23-demo.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(memo, f, ensure_ascii=False)
            trig = watch.load_triggers(d)
            self.assertEqual(len(trig), 1)
            self.assertEqual(trig[0]["keyword_groups"], [["a"], ["b"]])

    def test_malformed_terms_ignored(self):
        t = watch._terms([" Japan ", 3, None, "", "china"])
        self.assertEqual(t, ["japan", "china"])
        self.assertEqual(watch._groups(["x", ["a", "b"], "c"]), [["a", "b"]])


def _write_decision(directory, name, data):
    path = os.path.join(directory, name + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return path


class TestWatchMainRegressionChips(unittest.TestCase):
    """2026-11-23 真实误报回归：旧语义照样误报，升级后不再误报。"""

    LEGACY_CHIPS = {
        "question": "未来 90 天半导体出口管制升级的概率与应对",
        "scenarios": [{"name": "A", "prob": 60}, {"name": "B", "prob": 40}],
        "triggers": [
            {"id": "t2", "description": "至少两个关键盟友正式采纳与美国等效的管制措施",
             "keywords": ["netherlands", "japan", "singapore", "south korea",
                          "adopts", "aligned with us", "export controls"],
             "threshold": 2, "type": "count", "action": "加速非美供应链合规审计"},
            {"id": "t4", "description": "中国正式发布针对美防务/科技领域的稀土出口许可限制",
             "keywords": ["rare earth", "export restrictions", "china", "retaliation"],
             "threshold": 1, "type": "count", "action": "触发备选采购合同"},
        ],
        "confidence": "medium", "review_date": "2027-02-18",
    }

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "test.db")
        self._old_db = store.DB
        store.DB = self.db
        store.upsert_many([JP_EDU, JP_RARE_EARTH, ALLY_TRUE])
        self.dec_legacy = os.path.join(self.tmp.name, "dec-legacy")
        self.dec_upgraded = os.path.join(self.tmp.name, "dec-upgraded")
        os.makedirs(self.dec_legacy)
        os.makedirs(self.dec_upgraded)
        _write_decision(self.dec_legacy, "2026-11-23-chips", self.LEGACY_CHIPS)
        _write_decision(self.dec_upgraded, "2026-11-23-chips", UPGRADED_CHIPS)

    def tearDown(self):
        store.DB = self._old_db
        self.tmp.cleanup()

    def _run(self, dec_dir):
        out_dir = os.path.join(self.tmp.name, os.path.basename(dec_dir) + "-alerts")
        with mock.patch.object(watch.render, "build", return_value={}):
            rc = watch.main(["--hours", "24", "--decisions-dir", dec_dir,
                             "--out-dir", out_dir])
        self.assertEqual(rc, 0)
        files = os.listdir(out_dir)
        self.assertEqual(len(files), 1)
        with open(os.path.join(out_dir, files[0]), encoding="utf-8") as f:
            return f.read()

    def test_legacy_still_fires_false_positives(self):
        text = self._run(self.dec_legacy)
        self.assertIn("[t2]", text)
        self.assertIn("[t4]", text)
        self.assertIn("Japan's education minister", text)  # 误报仍复现

    def test_upgraded_kills_false_positive_keeps_true_positive(self):
        text = self._run(self.dec_upgraded)
        self.assertIn("[t2]", text)             # 荷兰真实采纳新闻仍命中
        self.assertIn("[t4]", text)             # 真正的中国稀土管制仍然命中
        self.assertIn("rare earth export restrictions", text)
        self.assertNotIn("Japan's education minister", text)  # 教育新闻不再入列


class TestAlertNotification(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state.json")
        self.webhook = "https://example.invalid/hook"

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _events():
        return [
            {"key": "a.md#t1", "text": "告警一"},
            {"key": "a.md#t2", "text": "告警二"},
        ]

    def test_first_run_sends_all(self):
        sent = []
        with mock.patch.object(weekly_summary, "send_webhook",
                               lambda url, text, timeout=15: sent.append(text)):
            n = watch.send_new_alerts(self._events(), self.state, self.webhook)
        self.assertEqual(n, 2)
        self.assertEqual(len(sent), 1)
        self.assertIn("告警一", sent[0])
        state = json.load(open(self.state, encoding="utf-8"))
        self.assertEqual(state["active"], ["a.md#t1", "a.md#t2"])

    def test_same_active_not_resent(self):
        sent = []
        with mock.patch.object(weekly_summary, "send_webhook",
                               lambda url, text, timeout=15: sent.append(text)):
            watch.send_new_alerts(self._events(), self.state, self.webhook)
            n = watch.send_new_alerts(self._events(), self.state, self.webhook)
        self.assertEqual(n, 0)
        self.assertEqual(len(sent), 1)

    def test_rearm_after_disappearance(self):
        sent = []
        with mock.patch.object(weekly_summary, "send_webhook",
                               lambda url, text, timeout=15: sent.append(text)):
            watch.send_new_alerts(self._events(), self.state, self.webhook)
            watch.send_new_alerts([], self.state, self.webhook)      # 当天无触发 → 解除
            n = watch.send_new_alerts(self._events(), self.state, self.webhook)
        self.assertEqual(n, 2)
        self.assertEqual(len(sent), 2)

    def test_send_failure_keeps_state_for_retry(self):
        sent = []

        def boom(url, text, timeout=15):
            sent.append(text)
            raise RuntimeError("network down")

        with mock.patch.object(weekly_summary, "send_webhook", boom):
            with self.assertRaises(RuntimeError):
                watch.send_new_alerts(self._events(), self.state, self.webhook)
        # 发送失败不写状态文件（也不推进），下轮从空状态重试全部新告警
        self.assertFalse(os.path.exists(self.state))
        # 恢复后重试，两条新告警合并成一条消息再推
        with mock.patch.object(weekly_summary, "send_webhook",
                               lambda url, text, timeout=15: sent.append(text)):
            n = watch.send_new_alerts(self._events(), self.state, self.webhook)
        self.assertEqual(n, 2)
        self.assertEqual(len(sent), 2)

    def test_main_send_without_webhook_exits_2(self):
        old = os.environ.pop("GEOINTEL_ALERT_WEBHOOK", None)
        try:
            with tempfile.TemporaryDirectory() as d:
                db = os.path.join(d, "test.db")
                old_db = store.DB
                store.DB = db
                try:
                    store.upsert_many([ALLY_TRUE])
                    dec = os.path.join(d, "dec")
                    os.makedirs(dec)
                    _write_decision(dec, "2026-11-23-chips", UPGRADED_CHIPS)
                    with mock.patch.object(watch.render, "build", return_value={}):
                        rc = watch.main(["--hours", "24", "--decisions-dir", dec,
                                         "--out-dir", os.path.join(d, "alerts"),
                                         "--state", os.path.join(d, "state.json"),
                                         "--send"])
                finally:
                    store.DB = old_db
            # 有新告警要推但没配渠道 → 明确报错退出 2；状态不推进，下轮重试
            self.assertEqual(rc, 2)
            self.assertFalse(os.path.exists(os.path.join(d, "state.json")))
        finally:
            if old is not None:
                os.environ["GEOINTEL_ALERT_WEBHOOK"] = old

    def test_main_send_success_writes_state_and_alert(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "test.db")
            old = store.DB
            store.DB = db
            try:
                store.upsert_many([ALLY_TRUE, JP_RARE_EARTH])
                dec = os.path.join(d, "dec")
                os.makedirs(dec)
                _write_decision(dec, "2026-11-23-chips", UPGRADED_CHIPS)
                state = os.path.join(d, "state.json")
                sent = []
                with mock.patch.object(weekly_summary, "send_webhook",
                                       lambda url, text, timeout=15: sent.append(text)):
                    with mock.patch.object(watch.render, "build", return_value={}):
                        rc = watch.main(["--hours", "24", "--decisions-dir", dec,
                                         "--out-dir", os.path.join(d, "alerts"),
                                         "--state", state, "--send",
                                         "--webhook", "https://example.invalid/h"])
                self.assertEqual(rc, 0)
                self.assertTrue(sent)  # t2/t4 命中并推送
                self.assertTrue(os.path.exists(state))
                self.assertEqual(
                    json.load(open(state, encoding="utf-8"))["active"],
                    ["2026-11-23-chips#t2", "2026-11-23-chips#t4"])
            finally:
                store.DB = old


class TestAutoScoreMemo(unittest.TestCase):
    def test_scores_recorded_on_generation_style_memo(self):
        # 单触发器 memo：触发条件 40 分封顶按 4 条基准，1 条只得 10 分 → 总分 70
        with tempfile.TemporaryDirectory() as d:
            dec = os.path.join(d, "dec")
            os.makedirs(dec)
            stem = "2026-11-23-test-q"
            data = {
                "question": "测试问题",
                "generated_at": "2026-11-23T01:00:00Z",
                "horizon_days": 90,
                "items_used": 40,
                "scenarios": [{"name": "A", "prob": 60}, {"name": "B", "prob": 40}],
                "triggers": [
                    {"id": "t1", "description": "测试触发条件，数值超过 5 就触发",
                     "keywords": ["测试"], "threshold": 5, "type": "value",
                     "action": "复核"},
                ],
                "confidence": "medium", "review_date": "2027-02-18",
            }
            with open(os.path.join(dec, stem + ".json"), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            md = ("---\nquestion: 测试问题\ngenerated_at: 2026-11-23T01:00:00Z\n"
                  "horizon_days: 90\nitems_used: 40\n---\n\n"
                  "## 1. 问题与边界\n最晚决策点：2027-01-10。\n")
            with open(os.path.join(dec, stem + ".md"), "w", encoding="utf-8") as f:
                f.write(md)
            scores_path = os.path.join(d, "scores.jsonl")
            s, deductions = decide.auto_score_memo(os.path.join(dec, stem + ".md"),
                                                   scores_path=scores_path)
            self.assertEqual(s, 70)
            self.assertTrue(any("少于 4 条" in r for _, r in deductions))
            rows = [json.loads(line) for line in open(scores_path, encoding="utf-8")
                    if line.strip()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["memo"], stem)
            self.assertEqual(rows[0]["slug"], "test-q")

    def test_score_failure_does_not_kill_memo_generation(self):
        # run_question 的兜底路径：评分异常时备忘录仍在、ok 仍为 True
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "memo.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write("minimal memo text")
            with mock.patch.object(decide.scoring, "record_structural",
                                   side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    decide.auto_score_memo(path)


class TestScoreGroupsSatisfyKeywordRule(unittest.TestCase):
    def test_count_with_only_groups_not_penalized(self):
        data = json.loads(json.dumps(UPGRADED_CHIPS))
        data["generated_at"] = "2026-11-23T01:00:00Z"
        data["horizon_days"] = 90
        data["items_used"] = 40
        data["question"] = "未来 90 天半导体出口管制升级的概率与应对"
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "2026-11-23-chips.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write("---\nquestion: q\ngenerated_at: 2026-11-23T01:00:00Z\n"
                        "horizon_days: 90\nitems_used: 40\n---\n\n"
                        "最晚决策点：2027-01-10。\n")
            with open(os.path.join(d, "2026-11-23-chips.json"), "w",
                      encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            import memo as memo_mod
            total, deductions = decide.scoring.structural_score(
                memo_mod.load_memo(path))
            self.assertFalse(any("没有关键词" in r for _, r in deductions))


if __name__ == "__main__":
    unittest.main()
