import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import store  # noqa: E402
import kb  # noqa: E402

SRC = {
    "item:a1": "BIS added 140 entities to the Entity List on 2026-11-18.",
    "memo:2026-11-17-us-china-tariff-90d": "PIIE: 美国对华平均关税 35.2%，截至 2026-08-22。",
}


def fake_chat(entries):
    def chat(prompt, **kw):
        return "```json\n" + json.dumps({"entries": entries}, ensure_ascii=False) + "\n```"
    return chat


class VerifyTest(unittest.TestCase):
    def test_number_must_be_in_source(self):
        ok, _ = kb.verify_fact({"text": "BIS 于 2026-11-18 新增 140 个实体", "src": ["item:a1"]}, SRC)
        self.assertTrue(ok)
        ok, why = kb.verify_fact({"text": "BIS 新增 150 个实体", "src": ["item:a1"]}, SRC)
        self.assertFalse(ok)
        self.assertIn("150", why)

    def test_unknown_or_missing_source_rejected(self):
        self.assertFalse(kb.verify_fact({"text": "x", "src": []}, SRC)[0])
        self.assertFalse(kb.verify_fact({"text": "x", "src": ["item:zz"]}, SRC)[0])

    def test_number_only_in_other_source_rejected(self):
        ok, _ = kb.verify_fact({"text": "关税 35.2%", "src": ["item:a1"]}, SRC)
        self.assertFalse(ok)

    def test_indicator_without_full_caliber_is_review(self):
        e, _ = kb.verify_entry({"kind": "indicator", "title": "美国对华平均关税",
                                "facts": [{"text": "截至 2026-08-22 为 35.2%",
                                           "src": ["memo:2026-11-17-us-china-tariff-90d"]}],
                                "fields": {"定义": "贸易加权平均关税"}}, SRC)
        self.assertEqual(e["status"], "review")

    def test_unverified_point_dropped(self):
        e, dropped = kb.verify_entry({"kind": "org", "title": "BIS",
                                      "facts": [{"text": "新增 140 个实体", "src": ["item:a1"]}],
                                      "points": ["新增 140 个实体", "成立于 1987 年"]}, SRC)
        self.assertEqual(e["points"], ["新增 140 个实体"])
        self.assertTrue(dropped)


class DbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._db = store.DB
        store.DB = os.path.join(self.tmp.name, "t.db")
        kb.connect().close()

    def tearDown(self):
        store.DB = self._db
        self.tmp.cleanup()

    def test_update_creates_and_rejects(self):
        st = kb.update(chat=fake_chat([
            {"kind": "org", "title": "美国商务部工业与安全局 (BIS)", "aliases": ["BIS"],
             "facts": [{"text": "2026-11-18 将 140 个实体列入实体清单", "src": ["item:a1"]}]},
            {"kind": "org", "title": "编造机构", "facts": [{"text": "成立于 1999 年", "src": ["item:a1"]}]},
        ]), sources=SRC)
        self.assertEqual((st["created"], st["rejected"]), (1, 1))
        es = kb.all_entries()
        self.assertEqual(len(es), 1)
        self.assertEqual(es[0]["status"], "ok")

    def test_incremental_update_by_alias_marks_review_and_logs(self):
        kb.update(chat=fake_chat([{"kind": "org", "title": "BIS", "aliases": ["工业与安全局"],
                                   "facts": [{"text": "负责出口管制", "src": ["item:a1"]}]}]), sources=SRC)
        st = kb.update(chat=fake_chat([{"kind": "org", "title": "工业与安全局",
                                        "facts": [{"text": "新增 140 个实体", "src": ["item:a1"]}]}]),
                       sources=SRC)
        self.assertEqual((st["created"], st["updated"], st["review"]), (0, 1, 1))
        es = kb.all_entries()
        self.assertEqual(len(es), 1)
        self.assertEqual(len(es[0]["facts"]), 2)
        self.assertEqual(es[0]["status"], "review")
        con = kb.connect()
        self.assertEqual(len(kb.revisions(con, es[0]["slug"])), 2)
        con.close()
        self.assertTrue(kb.mark_reviewed(es[0]["slug"]))
        self.assertEqual(kb.all_entries()[0]["status"], "ok")

    def test_daily_cap(self):
        drafts = [{"kind": "term", "title": "术语%d" % i,
                   "facts": [{"text": "出现在材料里", "src": ["item:a1"]}]} for i in range(5)]
        st = kb.update(chat=fake_chat(drafts), sources=SRC, max_new=3)
        self.assertEqual(st["created"], 3)

    def test_build_site_and_linkify(self):
        kb.update(chat=fake_chat([{"kind": "org", "title": "BIS",
                                   "facts": [{"text": "新增 140 个实体", "src": ["item:a1"]}]}]), sources=SRC)
        written = {}
        search, names, links = kb.build_site(lambda rel, c: written.__setitem__(rel, c),
                                             lambda t, k, b, cur, big=True: b, lambda p: "/g" + p)
        self.assertIn("kb.html", written)
        self.assertIn("kb/bis.html", written)
        self.assertEqual(names, {"bis.html"})
        self.assertEqual(search[0]["kicker"], "知识库")
        out = kb.linkify('<p>BIS 发布新规，BIS 再次</p><a href="x">BIS</a><p>BISX</p>', links)
        self.assertEqual(out.count('class="kb-link"'), 1)
        self.assertIn("BISX", out)


if __name__ == "__main__":
    unittest.main()
