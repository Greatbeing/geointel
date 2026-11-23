import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import llm  # noqa: E402


class _Stop(Exception):
    pass


class GatewayUrlTest(unittest.TestCase):
    def _url(self, **kw):
        seen = []

        def fake(url, *a, **k):
            seen.append(url)
            raise _Stop()

        with mock.patch.object(llm, "_post", side_effect=fake):
            with self.assertRaises(_Stop):
                llm._once("m", [{"role": "user", "content": "hi"}], 16000, 5, **kw)
        return seen[0]

    def test_local_gateway_uses_v1(self):
        self.assertEqual(self._url(), "http://127.0.0.1:3425/v1/chat/completions")

    def test_fallback_base_url_not_double_v1(self):
        self.assertEqual(self._url(base_url="https://api.example.com/v1", api_key="x"),
                         "https://api.example.com/v1/chat/completions")


if __name__ == "__main__":
    unittest.main()
