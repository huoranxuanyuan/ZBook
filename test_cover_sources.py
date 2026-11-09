# SPDX-License-Identifier: AGPL-3.0-only
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import hashlib
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
import covers
import cover_sources
import cover_browser
import requests
from test_covers_hd import picture


def response(html="", data=None, url="https://www.ypshuo.com/search"):
    return SimpleNamespace(text=html, url=url, json=lambda: data)


class CoverSourceTests(unittest.TestCase):
    def test_transient_connection_retries_once(self):
        result = requests.Response()
        result.status_code = 200
        result._content = b"<html></html>"
        with patch.object(
            covers.safe_network, "get", side_effect=[requests.Timeout(), result]
        ) as get:
            self.assertIs(covers._get("https://example.com/search"), result)
        self.assertEqual(get.call_count, 2)

    def test_http_rejection_is_not_retried(self):
        result = requests.Response()
        result.status_code = 403
        result._content = b"Forbidden"
        with patch.object(
            covers.safe_network, "get", side_effect=requests.HTTPError(response=result)
        ) as get:
            with self.assertRaises(requests.HTTPError):
                covers._get("https://example.com/search")
        self.assertEqual(get.call_count, 1)

    def test_transient_image_connection_retries_once(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(covers, "CACHE", Path(folder)),
            patch.object(
                covers, "_fetch", side_effect=[requests.ConnectionError(), picture((600, 800))]
            ) as fetch,
        ):
            result = covers._cache_row(dict(label="测试书", url="https://example.com/cover.jpg"))
        self.assertEqual((result.width, result.height), (600, 800))
        self.assertEqual(fetch.call_count, 2)

    def test_refresh_retains_previous_success_when_network_has_no_image(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder)
            image = cache / "last-success.jpg"
            image.write_bytes(picture((600, 800)))
            cover = covers.Cover(
                str(image),
                "测试书",
                "https://example.com/book",
                "https://example.com/cover.jpg",
                600,
                800,
                "书籍站点",
                "甲",
                "书名、作者匹配",
            )
            key = hashlib.sha256(("v4-sources\0测试书\0甲").encode("utf-8")).hexdigest()
            (cache / (key + ".json")).write_text(json.dumps([cover.__dict__]), encoding="utf-8")
            with (
                patch.object(covers, "CACHE", cache),
                patch.object(covers, "PROVIDERS", {"书籍站点": lambda *args: []}),
            ):
                result = covers.search_details("测试书", "甲", True)
            self.assertEqual(result.covers[0].path, str(image))
            self.assertTrue(any("上次成功" in line for line in result.diagnostics))

    def test_book_metadata_author_must_come_from_detail(self):
        body = json.dumps(
            {
                "@type": "Book",
                "name": "测试书",
                "author": {"name": "甲"},
                "image": "https://example.com/original.jpg",
            }
        )
        html = '<script type="application/ld+json">' + body + "</script>"
        self.assertEqual(
            cover_sources.parse_book_metadata(html, "https://example.com/book", "测试书", "乙"), []
        )
        row = cover_sources.parse_book_metadata(html, "https://example.com/book", "测试书", "甲")[0]
        self.assertTrue(row["metadata_verified"])
        self.assertEqual(row["author"], "甲")
        self.assertNotIn("official", row)

    def test_book_graph_and_wrong_title(self):
        book = {
            "@type": ["Book"],
            "name": "测试书",
            "author": [{"name": "甲"}],
            "image": {"contentUrl": "https://example.com/original.webp"},
        }
        html = '<script type="application/ld+json">' + json.dumps({"@graph": [book]}) + "</script>"
        self.assertEqual(len(cover_sources.parse_book_metadata(html, "", "测试书", "甲")), 1)
        self.assertEqual(cover_sources.parse_book_metadata(html, "", "测试书外传", "甲"), [])

    def test_ypshuo_author_fallback_verifies_full_title(self):
        book = {
            "@type": "Book",
            "name": "人在东京，从生活系职业开始",
            "author": {"name": "测试作者"},
            "image": "https://example.com/cover.jpg",
        }
        search = '<a href="/novel/123.html">人在东京，从生活系职业开始</a><a href="/novel/999.html">其他作品</a>'
        get = unittest.mock.Mock(
            side_effect=[
                response("无结果"),
                response(search),
                response(
                    '<script type="application/ld+json">' + json.dumps(book) + "</script>",
                    url="https://www.ypshuo.com/novel/123.html",
                ),
            ]
        )
        rows = cover_sources.ypshuo(book["name"], "测试作者", get)
        self.assertEqual(len(rows), 1)
        self.assertEqual(get.call_args_list[1].args[1], {"keyword": "测试作者"})

    def test_catalog_uses_explicit_novel_metadata(self):
        html = '<meta property="og:novel:book_name" content="测试书"><meta property="og:novel:author" content="作者甲"><meta property="og:image" content="https://example.com/cover.jpg">'
        rows = cover_sources.parse_novel_metadata(
            html, "https://example.com/book", "测试书", "作者甲"
        )
        self.assertTrue(rows[0]["metadata_verified"])
        self.assertEqual(cover_sources.parse_novel_metadata(html, "", "测试书", "作者乙"), [])
        self.assertEqual(
            cover_sources.parse_novel_metadata(
                html.replace("og:novel:book_name", "og:title"), "", "测试书", "作者甲"
            ),
            [],
        )

    def test_publisher_link_is_discovered_and_reverified(self):
        book = {
            "@type": "Book",
            "name": "测试书",
            "author": {"name": "甲"},
            "image": "https://example.com/deleted.jpg",
        }
        detail = (
            '<script type="application/ld+json">'
            + json.dumps(book)
            + '</script><a href="https://www.ciweimao.com/book/123456">立即阅读</a>'
        )
        publisher = '<meta property="og:novel:book_name" content="测试书"><meta property="og:novel:author" content="甲"><meta property="og:image" content="https://example.com/real.jpg">'
        get = unittest.mock.Mock(
            side_effect=[
                response('<a href="/novel/123.html">测试书</a>'),
                response(detail, url="https://www.ypshuo.com/novel/123.html"),
                response(publisher, url="https://www.ciweimao.com/book/123456"),
            ]
        )
        rows = cover_sources.ypshuo("测试书", "甲", get)
        self.assertEqual(len(rows), 2)
        primary = next(row for row in rows if row.get("official"))
        self.assertEqual(primary["url"], "https://example.com/real.jpg")
        self.assertEqual(primary["author"], "甲")

    def test_publisher_wrong_author_is_rejected(self):
        book = {
            "@type": "Book",
            "name": "测试书",
            "author": {"name": "甲"},
            "image": "https://example.com/a.jpg",
        }
        detail = (
            '<script type="application/ld+json">'
            + json.dumps(book)
            + '</script><a href="https://www.ciweimao.com/book/123456">立即阅读</a>'
        )
        publisher = '<meta property="og:novel:book_name" content="测试书"><meta property="og:novel:author" content="乙"><meta property="og:image" content="https://example.com/wrong.jpg">'
        get = unittest.mock.Mock(
            side_effect=[
                response('<a href="/novel/123.html">测试书</a>'),
                response(detail),
                response(publisher, url="https://www.ciweimao.com/book/123456"),
            ]
        )
        rows = cover_sources.ypshuo("测试书", "甲", get)
        self.assertFalse(any(row.get("official") for row in rows))

    def test_catalog_preserves_larger_page_cover_and_small_og(self):
        html = '<meta property="og:novel:book_name" content="测试书"><meta property="og:novel:author" content="甲"><meta property="og:image" content="https://example.com/preview.jpg"><img alt="测试书封面" src="https://example.com/original.jpg"><img alt="别的书封面" src="https://example.com/wrong.jpg">'
        row = cover_sources.parse_novel_metadata(html, "", "测试书", "甲")[0]
        self.assertEqual(row["url"], "https://example.com/original.jpg")
        self.assertEqual(row["fallback"], "https://example.com/preview.jpg")

    def test_google_preserves_original_and_does_not_invent_author(self):
        href = "/imgres?" + urlencode(
            {
                "imgurl": "https://example.com/raw.jpg",
                "imgrefurl": "https://example.com/book",
                "w": 600,
                "h": 800,
            }
        )
        html = '<a href="' + href.replace("&", "&amp;") + '"><img alt="测试书免费在线阅读"></a>'
        rows = cover_sources.google_images(
            "测试书", "某作者", lambda *args: response(html, url="https://www.google.com/search")
        )
        self.assertEqual(rows[0]["url"], "https://example.com/raw.jpg")
        self.assertNotIn("author", rows[0])
        self.assertNotIn("metadata_verified", rows[0])

    def test_google_rendered_json_keeps_caption_source_and_pixels_together(self):
        record = [
            0,
            "image-id",
            ["https://example.com/thumb.jpg", 150, 120],
            ["https://example.com/original.jpg", 800, 600],
            None,
            0,
            "rgb(1,2,3)",
            None,
            0,
            {"2003": [None, "doc-id", "https://example.com/book", "测试书 作者甲 在线阅读"]},
        ]
        page = '<script>window.data={"image":' + json.dumps(record) + "};</script>"
        row = cover_sources.parse_google_images(page)[0]
        self.assertEqual((row["width"], row["height"]), (600, 800))
        self.assertEqual(row["source"], "https://example.com/book")
        self.assertEqual(row["fallback"], "https://example.com/thumb.jpg")
        self.assertEqual(row["label"], "测试书 作者甲 在线阅读")
        self.assertNotIn("author", row)

    def test_search_caption_author_prioritizes_but_does_not_verify(self):
        correct = dict(
            label="测试书 作者甲 免费在线阅读",
            url="https://example.com/correct.jpg",
            width=120,
            height=150,
        )
        other = dict(
            label="测试书 最新章节在线阅读",
            url="https://example.com/other.jpg",
            width=600,
            height=800,
        )
        ranked = covers.rank_rows([other, correct], "测试书", "作者甲")
        self.assertEqual(ranked[0]["url"], correct["url"])
        self.assertEqual(ranked[0]["match"], "图片候选，作者待核对")

    def test_browser_query_is_encoded_and_sources_are_fixed(self):
        url = cover_browser.search_url("Google 图片", "书名&other=1", "作者")
        self.assertIn("www.google.com/search?", url)
        self.assertIn("%26other%3D1", url)
        with self.assertRaises(ValueError):
            cover_browser.search_url("任意网站", "书名")

    def test_google_verification_is_not_an_empty_search(self):
        with self.assertRaisesRegex(ValueError, "浏览器验证"):
            cover_sources.google_images(
                "测试书", "", lambda *args: response("var challenge_version=1;")
            )

    def test_baidu_verification_and_plain_original(self):
        with self.assertRaisesRegex(ValueError, "限制自动请求"):
            cover_sources.baidu_images("测试书", "", lambda *args: response(data={"antiFlag": 1}))
        item = {
            "fromPageTitle": "<em>测试书</em>小说封面",
            "objURL": "encoded-value",
            "replaceUrl": [
                {
                    "ObjURL": "https://example.com/original.jpg",
                    "FromURL": "https://example.com/book",
                }
            ],
            "middleURL": "https://example.com/thumb.jpg",
            "width": 600,
            "height": 800,
        }
        rows = cover_sources.baidu_images(
            "测试书", "某作者", lambda *args: response(data={"data": [item, {}]})
        )
        self.assertEqual(rows[0]["url"], "https://example.com/original.jpg")
        self.assertEqual(rows[0]["label"], "测试书小说封面")
        self.assertNotIn("author", rows[0])

    def test_verified_metadata_outranks_same_image_from_search(self):
        image = "https://example.com/cover.jpg"
        candidate = dict(label="测试书", url=image, provider="Google 图片")
        verified = dict(
            label="测试书",
            author="甲",
            url=image,
            provider="阅评说（书评站）",
            metadata_verified=True,
        )
        ranked = covers.rank_rows([candidate, verified], "测试书", "甲")
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["provider"], verified["provider"])
        self.assertEqual(ranked[0]["match"], "书名、作者匹配")
        self.assertEqual(covers.rank_rows([verified], "测试书", "乙"), [])
        self.assertEqual(covers.rank_rows([dict(verified, label="测试书外传")], "测试书", "甲"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
