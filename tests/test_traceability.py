#!/usr/bin/env python3
"""paid-ready 第 3 步测试：可追溯性——结论附原文链接与抓取时间。

覆盖：
- decide.material：材料行带原文链接（LLM 可直接引用）
- decide.run_question：备忘录 JSON 落盘 materials 索引（编号/来源/日期/链接）
- render.materials_section：有 materials 渲染出处清单（链接可点）；
  旧备忘录无该字段时不输出空节
- digest 模板包含出处标注硬规则
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import decide  # noqa: E402
import digest  # noqa: E402
import render  # noqa: E402


ITEMS = [
    {"source": "csis", "published": "2026-11-22T12:00:00Z",
     "title": "Netherlands adopts export controls", "summary": "measures",
     "link": "https://example.org/ally"},
    {"source": "nikkei-asia", "published": "2026-11-22T11:00:00Z",
     "title": "Rare earth update", "summary": "licensing",
     "link": "https://example.org/rare"},
    {"source": "reuters", "published": "2026-11-22T10:00:00Z",
     "title": "No link item", "summary": "s", "link": ""},
]


class TestMaterialLinks(unittest.TestCase):
    def test_material_lines_include_links(self):
        text = decide.material(ITEMS)
        self.assertIn("https://example.org/ally", text)
        self.assertIn("https://example.org/rare", text)
        # 无链接的条目不悬挂多余空格结尾
        self.assertIn("No link item", text)

    def test_material_prompt_mentions_links(self):
        # 模板要告诉模型材料已带链接，模型才会引用它
        self.assertIn("原文链接", decide.TEMPLATE)
        self.assertIn("[n]", decide.TEMPLATE)


class TestMemoMaterialsIndex(unittest.TestCase):
    NOW = datetime(2026, 11, 23, 1, 0, 0, tzinfo=timezone.utc)

    def _run(self):
        meta = {
            "scenarios": [{"name": "A", "prob": 60}, {"name": "B", "prob": 40}],
            "triggers": [{"id": "t1", "description": "测试触发条件描述足够长",
                          "keywords": ["x"], "threshold": 1, "type": "count",
                          "action": "动作"}],
            "confidence": "medium", "review_date": "2027-02-21",
        }
        body = ("```json\n" + json.dumps(meta, ensure_ascii=False) + "\n```\n\n"
                "# 正文\n\n## 1. 问题与边界\n最晚决策点：2027-01-01。\n")
        tmp = tempfile.mkdtemp()
        with mock.patch.object(decide.llm, "chat", return_value=body), \
             mock.patch.object(decide.store, "search", return_value=ITEMS), \
             mock.patch.object(decide.render, "build", return_value={}), \
             mock.patch.object(decide.scoring, "record_structural", return_value={}):
            slug, ok, detail = decide.run_question(
                {"question": "测试问题", "days": 30, "horizon": 90, "slug": "t1"},
                tmp, self.NOW)
        self.assertTrue(ok)
        with open(os.path.join(tmp, "2026-11-23-t1.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_materials_saved_with_links(self):
        saved = self._run()
        mats = saved.get("materials")
        self.assertEqual(len(mats), 3)
        self.assertEqual(mats[0]["n"], 1)
        self.assertEqual(mats[0]["source"], "csis")
        self.assertEqual(mats[0]["published"], "2026-11-22")
        self.assertEqual(mats[0]["link"], "https://example.org/ally")
        self.assertEqual(mats[2]["link"], "")  # 无链接条目保留空串
        self.assertEqual([m["n"] for m in mats], [1, 2, 3])


class TestMaterialsSection(unittest.TestCase):
    def test_renders_links_and_dates(self):
        extra = {"materials": [
            {"n": 1, "title": "Netherlands adopts export controls", "source": "csis",
             "published": "2026-11-22", "link": "https://example.org/ally"},
            {"n": 2, "title": "Rare earth update", "source": "nikkei-asia",
             "published": "2026-11-22", "link": "https://example.org/rare"},
        ]}
        out = render.materials_section(extra)
        self.assertIn("材料出处", out)
        self.assertIn('href="https://example.org/ally"', out)
        self.assertIn('href="https://example.org/rare"', out)
        self.assertIn("[1]", out)
        self.assertIn("[2]", out)
        self.assertIn("csis", out)
        self.assertIn("2026-11-22", out)

    def test_no_materials_no_section(self):
        self.assertEqual(render.materials_section({}), "")
        self.assertEqual(render.materials_section({"materials": []}), "")
        self.assertEqual(render.materials_section({"materials": "not-a-list"}), "")

    def test_linkless_item_still_listed(self):
        extra = {"materials": [
            {"n": 3, "title": "No link item", "source": "reuters",
             "published": "2026-11-22", "link": ""},
        ]}
        out = render.materials_section(extra)
        self.assertIn("No link item", out)
        self.assertNotIn('href=""', out)

    def test_bad_rows_skipped(self):
        extra = {"materials": ["junk", {"n": 1, "title": "ok", "link": "https://x.org"}]}
        out = render.materials_section(extra)
        self.assertIn("https://x.org", out)
        self.assertNotIn("junk", out)

    def test_rendered_page_contains_materials(self):
        # render.build 集成：memo 页正文后接材料出处清单
        with tempfile.TemporaryDirectory() as d:
            import store
            old_db = store.DB
            render.OUT = os.path.join(d, "out")
            render.SITE = os.path.join(d, "docs")
            store.DB = os.path.join(d, "intel.db")
            try:
                dec = os.path.join(render.OUT, "decisions")
                os.makedirs(dec)
                meta = {"question": "q", "confidence": "medium",
                        "materials": [
                            {"n": 1, "title": "Some title", "source": "csis",
                             "published": "2026-11-22", "link": "https://example.org/a"}]}
                with open(os.path.join(dec, "2026-11-23-demo.json"), "w",
                          encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False)
                with open(os.path.join(dec, "2026-11-23-demo.md"), "w",
                          encoding="utf-8") as f:
                    f.write("---\nquestion: q\n---\n\n# 标题\n\n正文。\n")
                # 不给 scores.jsonl：load_scores 走文件缺失分支
                render.build()
                with open(os.path.join(render.SITE, "decisions", "2026-11-23-demo.html"),
                          encoding="utf-8") as _f:
                    page = _f.read()
                self.assertIn("材料出处", page)
                self.assertIn("https://example.org/a", page)
            finally:
                # render.build 的 keep-清理逻辑依赖 OUT/SITE 的相对关系，恢复
                render.OUT = os.path.join(ROOT, "out")
                render.SITE = os.path.join(ROOT, "docs")
                store.DB = old_db


class TestDigestTraceability(unittest.TestCase):
    def test_template_requires_source_date_attribution(self):
        self.assertIn("（来源，日期）", digest.TEMPLATE)
        self.assertIn("链接", digest.TEMPLATE)

    def test_digest_material_carries_links(self):
        text = digest.material(ITEMS)
        self.assertIn("https://example.org/ally", text)


if __name__ == "__main__":
    unittest.main()
