import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import search_index as si  # noqa: E402


class TokenTest(unittest.TestCase):
    def test_cjk_bigrams(self):
        t = si.tokens("出口管制升级")
        self.assertIn("出口", t)
        self.assertIn("管制", t)
        self.assertIn("出口管制升级", t)

    def test_ascii_lowercased_and_stopwords_dropped(self):
        t = si.tokens("BIS Entity List 的")
        self.assertIn("bis", t)
        self.assertIn("entity", t)
        self.assertNotIn("的", t)


class IndexTest(unittest.TestCase):
    def test_build_and_strip(self):
        idx = si.build_index([{"title": "芯片", "kicker": "k", "url": "/a.html", "date": "2026-11-01",
                               "text": "## 标题\n[链接](http://x) **粗体** `code`"}])
        self.assertEqual(len(idx), 1)
        self.assertNotIn("**", idx[0]["x"])
        self.assertNotIn("http://x", idx[0]["x"])
        self.assertIn("链接", idx[0]["x"])

    def test_write_search_outputs(self):
        import render
        with tempfile.TemporaryDirectory() as d:
            n = si.write_search(d, [{"title": "t", "kicker": "k", "url": "/u", "date": "2026-01-01", "text": "x"}],
                                render.CSS, "/geointel/index.html", render.WORDMARK, "<a>x</a>")
            self.assertEqual(n, 1)
            with open(os.path.join(d, "search.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)[0]["u"], "/u")
            with open(os.path.join(d, "search.html"), encoding="utf-8") as f:
                page = f.read()
            self.assertIn("search.json", page)
            self.assertNotIn("__CSS__", page)


if __name__ == "__main__":
    unittest.main()
