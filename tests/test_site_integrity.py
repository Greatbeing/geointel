"""站点完整性：静态链接不坏、知识库数据自洽。CI 上防回归。"""
import json, os, re, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DOCS = os.path.join(ROOT, "docs")
PREFIX = "/geointel/"


def broken_links(docs=DOCS):
    bad = []
    for dp, _, fs in os.walk(docs):
        for f in fs:
            if not f.endswith(".html"):
                continue
            p = os.path.join(dp, f)
            h = open(p, encoding="utf-8").read()
            h = re.sub(r"<script\b.*?</script>", "", h, flags=re.S)  # JS 模板不算
            for u in re.findall(r'(?:href|src)="([^"#?]+)', h):
                if re.match(r"^[a-z]+:", u) or "+" in u or "{" in u:
                    continue
                if u.startswith(PREFIX):
                    t = os.path.join(docs, u[len(PREFIX):])
                elif u.startswith("/"):
                    t = os.path.join(docs, u[1:])
                else:
                    t = os.path.normpath(os.path.join(dp, u))
                if u.endswith("/"):
                    t = os.path.join(t, "index.html")
                if not os.path.exists(t):
                    bad.append((os.path.relpath(p, docs), u))
    return bad


class SiteLinks(unittest.TestCase):
    @unittest.skipUnless(os.path.isdir(DOCS), "no docs/")
    def test_no_broken_internal_links(self):
        bad = broken_links()
        self.assertEqual(bad, [], "broken links: %r" % bad[:10])


class KbRefsIntegrity(unittest.TestCase):
    def setUp(self):
        import tempfile, store, kb_refs
        self.tmp = tempfile.TemporaryDirectory()
        self._old = store.DB
        store.DB = os.path.join(self.tmp.name, "intel.db")
        self.kr = kb_refs
        self.con = kb_refs.connect()
        now = "2026-01-01T00:00:00Z"
        rows = [("t-finance-npv", "term", "valuation", "core"),
                ("t-finance-irr", "term", "valuation", "supporting"),
                ("t-macro-gdp", "term", "macro", "core"),
                ("e-finance", "theme", "金融学", "core"),
                ("e-economics", "theme", "经济学", "core")]
        with self.con:
            for slug, layer, kind, imp in rows:
                self.con.execute(
                    "INSERT INTO kb_refs(slug,layer,kind,title_zh,title_en,abbr,definition,"
                    "formula,why,caveat,related,importance,path,updated)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (slug, layer, kind, slug, "", "", "d", "", "w", "", "[]", imp, "", now))

    def tearDown(self):
        import store
        self.con.close()
        store.DB = self._old
        self.tmp.cleanup()

    def _rel(self, slug):
        return json.loads(self.con.execute(
            "SELECT related FROM kb_refs WHERE slug=?", (slug,)).fetchone()[0])

    def test_link_themes(self):
        self.assertEqual(self.kr.link_themes(self.con), 2)
        self.assertEqual(self._rel("e-finance"), ["t-finance-npv", "t-finance-irr"])
        self.assertIn("t-macro-gdp", self._rel("e-economics"))

    def test_no_dangling_related(self):
        self.kr.link_themes(self.con)
        slugs = {r[0] for r in self.con.execute("SELECT slug FROM kb_refs")}
        for slug, rel in self.con.execute("SELECT slug, related FROM kb_refs"):
            for t in json.loads(rel):
                self.assertIn(t, slugs, (slug, t))


if __name__ == "__main__":
    unittest.main()
