# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
import os
import sys
import unittest
import uuid
from unittest.mock import patch
import requests

WORK = Path(__file__).resolve().parent
sys.path.append(str(Path(__file__).resolve().parent))
os.environ["ZBOOK_DATA_DIR"] = str(WORK / "archive/cover-state-tests")
import covers


class CoverStateTests(unittest.TestCase):
    def setUp(self):
        folder = WORK / "archive/cover-state-tests" / uuid.uuid4().hex
        folder.mkdir(parents=True)
        self.cache_patch = patch.object(covers, "CACHE", folder)
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)

    def test_search_found_but_download_failed(self):
        row = {
            "label": "测试书名",
            "author": "测试作者",
            "official": True,
            "url": "https://example.com/cover.jpg",
            "provider": "书籍站点",
        }
        with (
            patch.object(covers, "PROVIDERS", {"书籍站点": lambda title, author: [row]}),
            patch.object(covers, "_cache_row", return_value=None),
        ):
            result = covers.search_details("测试书名", "测试作者")
        self.assertEqual(result.matched_count, 1)
        self.assertEqual(result.covers, [])
        self.assertTrue(any("图片可用 0" in line for line in result.diagnostics))

    def test_friendly_errors_do_not_block_other_sources(self):
        response = requests.Response()
        response.status_code = 404

        def http_failure(*args):
            raise requests.HTTPError(response=response)

        def invalid_json(*args):
            raise requests.exceptions.JSONDecodeError("Expecting value", "", 0)

        providers = {"站点甲": http_failure, "站点乙": invalid_json, "站点丙": lambda *args: []}
        with patch.object(covers, "PROVIDERS", providers):
            result = covers.search_details("测试书名")
        self.assertEqual(result.matched_count, 0)
        self.assertTrue(any("HTTP 404" in line for line in result.diagnostics))
        self.assertTrue(any("可能正在验证或限流" in line for line in result.diagnostics))
        self.assertTrue(any("站点丙：返回 0 条" in line for line in result.diagnostics))

    def test_all_sources_down_is_an_explicit_failure(self):
        def timeout(*args):
            raise requests.Timeout()

        with patch.object(covers, "PROVIDERS", {"站点甲": timeout}):
            with self.assertRaisesRegex(ConnectionError, "连接超时"):
                covers.search_details("测试书名")


if __name__ == "__main__":
    unittest.main(verbosity=2)
