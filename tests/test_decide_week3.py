#!/usr/bin/env python3
"""Unit tests for Week 3: decide template upgrade + render score display.
Stdlib only, no network, no LLM calls.
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import decide  # noqa: E402
import render  # noqa: E402
import store  # noqa: E402


class TestMarketTable(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "test.db")
        self._old = store.DB
        store.DB = self.db

    def tearDown(self):
        store.DB = self._old
        self.tmp.cleanup()

    def test_table_with_data(self):
        store.upsert_market([
            {"series": "DFF", "date": "2026-11-19", "value": 3.88, "source": "FRED"},
            {"series": "DGS10", "date": "2026-11-19", "value": 5.04, "source": "FRED"},
        ])
        table = decide.market_table()
        self.assertIn("美联邦基金利率", table)
        self.assertIn("3.88%", table)
        self.assertIn("2026-11-19", table)
        self.assertIn("美债 10 年收益率", table)

    def test_table_without_data(self):
        table = decide.market_table()
        self.assertIn("暂不可用", table)
        self.assertNotIn("3.88", table)


class TestTemplate(unittest.TestCase):
    def test_template_has_market_table_slot(self):
        self.assertIn("{market_table}", decide.TEMPLATE)

    def test_template_has_history_section(self):
        self.assertIn("历史周期代价对照", decide.TEMPLATE)

    def test_template_documents_series_trigger_fields(self):
        for token in ("series", "op", "window"):
            self.assertIn(token, decide.TEMPLATE)

    def test_template_requires_hard_data_consistency(self):
        self.assertIn("必须与表一致", decide.TEMPLATE)

    def test_template_requires_source_anchors(self):
        self.assertIn("[n]", decide.TEMPLATE)


class TestBuildPrompt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "test.db")
        self._old = store.DB
        store.DB = self.db
        store.upsert_market([
            {"series": "DFF", "date": "2026-11-19", "value": 3.88, "source": "FRED"},
        ])

    def tearDown(self):
        store.DB = self._old
        self.tmp.cleanup()

    def test_prompt_contains_question_material_and_market(self):
        items = [{"source": "ft", "published": "2026-11-20T00:00:00Z",
                  "title": "测试标题", "summary": "测试摘要"}]
        prompt = decide.build_prompt("测试问题？", 90, 30, items)
        self.assertIn("测试问题？", prompt)
        self.assertIn("[1]", prompt)
        self.assertIn("测试标题", prompt)
        self.assertIn("美联邦基金利率", prompt)
        self.assertIn("3.88%", prompt)


class TestRenderScores(unittest.TestCase):
    def test_load_scores(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "scores.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps({"memo": "2026-11-21-a", "structural": {"score": 95}},
                                  ensure_ascii=False) + "\n")
                f.write(json.dumps({"memo": "2026-11-21-b", "structural": {"score": 80}},
                                  ensure_ascii=False) + "\n")
                f.write("not json\n")
            self.assertEqual(render.load_scores(path),
                             {"2026-11-21-a": 95, "2026-11-21-b": 80})

    def test_load_scores_missing_file(self):
        self.assertEqual(render.load_scores("/tmp/does-not-exist-scores.jsonl"), {})

    def test_build_shows_score(self):
        with tempfile.TemporaryDirectory() as d:
            out_dir = os.path.join(d, "out")
            site_dir = os.path.join(d, "docs")
            os.makedirs(os.path.join(out_dir, "digest"))
            os.makedirs(os.path.join(out_dir, "decisions"))
            with open(os.path.join(out_dir, "digest", "2026-11-21.md"), "w",
                      encoding="utf-8") as f:
                f.write("---\ndate: 2026-11-21\n---\n\n## 今日要点\n测试。\n")
            memo_md = ("---\nquestion: 测试问题\ngenerated_at: 2026-11-21T04:11:02Z\n"
                       "horizon_days: 90\nitems_used: 40\n---\n\n# 测试问题\n\n## 1. 问题与边界\n测试。\n")
            with open(os.path.join(out_dir, "decisions", "2026-11-21-test-memo.md"), "w",
                      encoding="utf-8") as f:
                f.write(memo_md)
            with open(os.path.join(out_dir, "decisions", "2026-11-21-test-memo.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"question": "测试问题", "confidence": "medium",
                           "review_date": "2027-02-18",
                           "scenarios": [{"name": "A", "prob": 60}]},
                          f, ensure_ascii=False)
            scores_path = os.path.join(d, "scores.jsonl")
            with open(scores_path, "w", encoding="utf-8") as f:
                f.write(json.dumps({"memo": "2026-11-21-test-memo",
                                    "structural": {"score": 95}},
                                  ensure_ascii=False) + "\n")
            old_out, old_site, old_here = render.OUT, render.SITE, render.HERE
            old_db = store.DB
            render.OUT, render.SITE, render.HERE = out_dir, site_dir, d
            # 知识库渲染会打开 store.DB；CI 上没有生产库路径，隔离到临时库
            store.DB = os.path.join(d, "intel.db")
            try:
                # load_scores 用 HERE 拼默认路径；让它指向临时 scores.jsonl
                import unittest.mock as mock
                with mock.patch.object(render, "load_scores",
                                       return_value={"2026-11-21-test-memo": 95}):
                    render.build()
            finally:
                render.OUT, render.SITE, render.HERE = old_out, old_site, old_here
                store.DB = old_db
            with open(os.path.join(site_dir, "decisions.html"), encoding="utf-8") as _f:
                index = _f.read()
            self.assertIn("结构分 95/100", index)
            with open(os.path.join(site_dir, "decisions", "2026-11-21-test-memo.html"),
                      encoding="utf-8") as _f:
                memo_page = _f.read()
            self.assertIn("结构分 95/100", memo_page)


if __name__ == "__main__":
    unittest.main()
