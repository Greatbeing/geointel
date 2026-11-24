import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import store  # noqa: E402
import kb_refs  # noqa: E402

TS = '''
import type { Concept } from "../types";

export const macro: Concept[] = [
  {
    slug: "gdp",
    zh: "国内生产总值",
    en: "Gross Domestic Product",
    abbr: "GDP",
    chapter: "macro",
    importance: "core",
    definition:
      "一定时期内一国境内最终产品与服务的市场价值。",
    formula: "Y=C+I+G+(X-M)",
    why: "增长预期进入盈利与风险溢价。",
    caveat: "GDP 是滞后、会修订的总量。",
    related: ["inflation"],
  },
  {
    slug: "inflation",
    zh: "通货膨胀",
    en: "Inflation",
    chapter: "macro",
    importance: "core",
    definition:
      "一般价格水平持续上升、货币购买力下降。",
    related: ["gdp"],
  },
];
'''

BOOK = '''# 金融法律与交易规则

## 相关主题

## 1. 核心范围
### 1.1 法律的定义与功能
### 1.2 法律与金融的关系
## 2. 中美对比
### 2.1 公司法对比
'''


class ParseTsTest(unittest.TestCase):
    def test_parses_entries_and_multiline_strings(self):
        es = kb_refs.parse_ts_entries(TS)
        self.assertEqual([e["slug"] for e in es], ["gdp", "inflation"])
        self.assertEqual(es[0]["zh"], "国内生产总值")
        self.assertEqual(es[0]["abbr"], "GDP")
        self.assertIn("最终产品", es[0]["definition"])
        self.assertIn("Y=C+I+G", es[0]["formula"])
        self.assertEqual(es[0]["related"], ["inflation"])
        self.assertEqual(es[1]["related"], ["gdp"])

    def test_outline_skips_nav_headings(self):
        out = kb_refs.parse_outline(BOOK)
        texts = [o["text"] for o in out]
        self.assertNotIn("相关主题", texts)
        self.assertIn("1. 核心范围", texts)
        self.assertEqual(out[0]["level"], 1)


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._db = store.DB
        store.DB = os.path.join(self.tmp.name, "kb.db")
        kb_refs.connect().close()
        self.book = os.path.join(self.tmp.name, "book")
        os.makedirs(self.book)
        with open(os.path.join(self.book, "04-law.md"), "w", encoding="utf-8") as f:
            f.write(BOOK)

    def tearDown(self):
        store.DB = self._db
        self.tmp.cleanup()

    def test_terms_import_and_related_resolution(self):
        con = kb_refs.connect()
        n = kb_refs.import_terms(con, {"macro": TS})
        con.close()
        self.assertEqual(n, 2)
        refs = kb_refs.all_refs()
        self.assertEqual(len(refs), 2)
        by_zh = {r["title_zh"]: r for r in refs}
        gdp = by_zh["国内生产总值"]
        self.assertEqual(gdp["kind"], "macro")
        self.assertEqual(gdp["layer"], "term")
        self.assertEqual(gdp["related"], ["t-macro-inflation"])

    def test_idempotent(self):
        con = kb_refs.connect()
        kb_refs.import_terms(con, {"macro": TS})
        kb_refs.import_terms(con, {"macro": TS})
        con.close()
        self.assertEqual(len(kb_refs.all_refs()), 2)

    def test_theme_import(self):
        con = kb_refs.connect()
        kb_refs.import_themes(con, self.book)
        con.close()
        th = kb_refs.themes(kb_refs.all_refs())
        self.assertEqual(len(th), 1)
        self.assertEqual(th[0]["title_zh"], "金融法律与交易规则")
        self.assertIn("1. 核心范围", th[0]["caveat"])
        self.assertIn("1.1 法律的定义与功能", th[0]["caveat"])

    def test_render_and_link_table(self):
        con = kb_refs.connect()
        kb_refs.import_terms(con, {"macro": TS})
        con.close()
        refs = kb_refs.all_refs()
        written = {}
        search, names = kb_refs.build_site(
            lambda rel, c: written.__setitem__(rel, c),
            lambda t, k, b, cur, big=True: b, lambda p: "/g" + p, titles={})
        self.assertIn("kb/t-macro-gdp.html", written)
        self.assertEqual(len(names), 2)
        self.assertEqual(len(search), 2)
        body = written["kb/t-macro-gdp.html"]
        self.assertIn("国内生产总值", body)
        self.assertIn("Y=C+I+G", body)
        self.assertIn('href="/g/kb/t-macro-inflation.html"', body)
        table = kb_refs.link_table(refs, lambda p: "/g" + p)
        self.assertIn("GDP", table)
        self.assertIn("国内生产总值", table)
        self.assertNotIn("", table)

    def test_index_lists_groups_and_themes(self):
        con = kb_refs.connect()
        kb_refs.import_terms(con, {"macro": TS})
        kb_refs.import_themes(con, self.book)
        con.close()
        html = kb_refs.refs_index_html(kb_refs.all_refs(), lambda p: p)
        self.assertIn("术语（中英对照）", html)
        self.assertIn("宏观、利率与政策", html)
        self.assertIn("主题框架", html)
        self.assertIn("金融法律与交易规则", html)


if __name__ == "__main__":
    unittest.main()