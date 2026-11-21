#!/usr/bin/env python3
"""Unit tests for geointel sources, lexicon expansion, FTS retrieval, and questions config."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import collect  # noqa: E402
import questions  # noqa: E402
import store  # noqa: E402


class TestSourcesConfig(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "sources.json"), encoding="utf-8") as f:
            self.data = json.load(f)
        self.sources = self.data["sources"]

    def test_unique_ids_and_required_fields(self):
        seen = set()
        for src in self.sources:
            for field in ("id", "name", "url", "region", "category", "weight"):
                self.assertIn(field, src, f"missing {field} in {src}")
            self.assertNotIn(src["id"], seen, f"duplicate source id: {src['id']}")
            seen.add(src["id"])
            self.assertTrue(
                src["url"].startswith(("https://", "http://")),
                f"invalid url for {src['id']}: {src['url']}",
            )
            self.assertGreater(float(src["weight"]), 0.0)

    def test_ifi_and_macro_policy_sources_present(self):
        cats = {s["category"] for s in self.sources}
        self.assertIn("ifi", cats)
        self.assertIn("macro_policy", cats)
        ids = {s["id"] for s in self.sources}
        for required_id in ("adb-news", "fsb", "boj", "boe", "bis-speeches", "gn-wb-imf", "gn-aiib-ndb"):
            self.assertIn(required_id, ids)


class TestQuestionsConfig(unittest.TestCase):
    def test_questions_json_matches_module_defaults(self):
        with open(os.path.join(ROOT, "questions.json"), encoding="utf-8") as f:
            qs = json.load(f)["questions"]
        self.assertEqual(qs, questions.QUESTIONS)
        self.assertEqual(len(qs), 4)
        slugs = [q["slug"] for q in qs]
        self.assertEqual(len(slugs), len(set(slugs)))
        for q in qs:
            self.assertTrue(q["question"].strip())
            self.assertGreater(q["horizon"], 0)
            self.assertGreater(q["days"], 0)
            self.assertGreater(q["items"], 0)


class TestStoreLexiconAndSearch(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.orig_db = store.DB
        store.DB = os.path.join(self.tmpdir.name, "test_intel.db")

    def tearDown(self):
        store.DB = self.orig_db
        self.tmpdir.cleanup()

    def test_expand_filters_chinese_noise_and_keeps_acronyms(self):
        terms = store.expand("未来 90 天世界银行与 ADB 在环社框架（ESF）和 SOFR 方面的政策")
        lower = {t.lower() for t in terms}
        self.assertIn("adb", lower)
        self.assertIn("esf", lower)
        self.assertIn("sofr", lower)
        self.assertIn("world bank", lower)
        self.assertIn("safeguards", lower)
        # No raw Chinese chunks should occupy FTS query slots
        for t in terms:
            self.assertTrue(all(ord(c) < 128 for c in t), f"non-ASCII token leaked: {t}")

    def test_all_configured_questions_expand_to_rich_terms(self):
        with open(os.path.join(ROOT, "questions.json"), encoding="utf-8") as f:
            qs = json.load(f)["questions"]
        for q in qs:
            terms = store.expand(q["question"])
            self.assertGreaterEqual(len(terms), 6, f"too few terms for {q['slug']}: {terms}")

    def test_fts_search_hits_ifi_english_corpus(self):
        now = store.iso(store.now_utc())
        sample_items = [
            {
                "id": "ifi-1",
                "source": "adb-news",
                "region": "asia",
                "category": "ifi",
                "title": "ADB Approves $200 Million Climate Finance Loan Under Updated Safeguards",
                "link": "https://example.org/adb-1",
                "summary": "Asian Development Bank approves sovereign loan with procurement and ESF guidelines.",
                "published": now,
                "weight": 1.3,
            },
            {
                "id": "macro-1",
                "source": "gn-fx-rates",
                "region": "global",
                "category": "macro_policy",
                "title": "Federal Reserve and BOJ divergence shifts SOFR and yuan exchange rate",
                "link": "https://example.org/fx-1",
                "summary": "USD/CNY and sovereign bond yield spreads adjust to central bank tightening.",
                "published": now,
                "weight": 1.2,
            },
            {
                "id": "noise-1",
                "source": "hn",
                "region": "global",
                "category": "tech",
                "title": "New Rust compiler optimization reduces binary size",
                "link": "https://example.org/rust-1",
                "summary": "Unrelated software engineering post.",
                "published": now,
                "weight": 0.7,
            },
        ]
        inserted = store.upsert_many(sample_items)
        self.assertEqual(inserted, 3)

        res_mdb = store.search("亚行 气候融资 采购 环社框架", limit=1, days=30)
        self.assertEqual(res_mdb[0]["id"], "ifi-1")

        res_fx = store.search("美联储 日央行 利率 人民币 汇率", limit=1, days=30)
        self.assertEqual(res_fx[0]["id"], "macro-1")


class TestCollectHelpers(unittest.TestCase):
    def test_canonical_strips_tracking_params(self):
        url = "https://www.worldbank.org/en/news/feature/?utm_source=rss&id=42&fbclid=abc"
        self.assertEqual(collect.canonical(url), "https://www.worldbank.org/en/news/feature?id=42")

    def test_parse_feed_handles_html_entities(self):
        xml = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b"<rss><channel><item>"
            b"<title>World Bank &mdash; Climate &nbsp; Loan</title>"
            b"<link>https://www.worldbank.org/test?utm_medium=email</link>"
            b"<description>SOFR &amp; sovereign debt sustainability</description>"
            b"<pubDate>Fri, 20 Nov 2026 12:00:00 +0000</pubDate>"
            b"</item></channel></rss>"
        )
        items = collect.parse_feed(xml, {"id": "wb-test", "region": "global", "category": "ifi", "weight": 1.3})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["link"], "https://www.worldbank.org/test")
        self.assertIn("Climate", items[0]["title"])


if __name__ == "__main__":
    unittest.main()
