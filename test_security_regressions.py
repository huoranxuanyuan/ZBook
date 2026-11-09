# SPDX-License-Identifier: AGPL-3.0-only
import io
import os
from pathlib import Path
import shutil
import socket
import tempfile
import time
import unittest
import zipfile
from unittest.mock import patch
import requests
from requests.adapters import BaseAdapter
from PIL import Image, UnidentifiedImageError
import safe_network
from safe_image import open_image
from safe_regex import HeadingPattern
from input_sources import read_document
from cache_maintenance import CacheMaintenance, DAY
import cover_browser


class Responses(BaseAdapter):
    def __init__(self, rows):
        self.rows = iter(rows)
        self.urls = []

    def send(self, request, **kwargs):
        self.urls.append(request.url)
        status, headers, body = next(self.rows)
        r = requests.Response()
        r.status_code = status
        r.headers.update(headers)
        r.url = request.url
        r.request = request
        r.raw = io.BytesIO(body)
        return r

    def close(self):
        pass


def session(rows):
    s = safe_network.PublicSession()
    s.trust_env = False
    adapter = Responses(rows)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s, adapter


class SecurityTests(unittest.TestCase):
    def test_redirect_cannot_reach_loopback(self):
        for target in (
            "http://127.0.0.1/private",
            "http://[::1]/",
            "http://localhost./",
            "file:///secret",
            "http://user:pass@example.com/",
        ):
            with self.subTest(target=target):
                s, a = session([(302, {"Location": target}, b"")])
                with s, self.assertRaises(ValueError):
                    safe_network.get("https://example.com/", session=s)
                self.assertEqual(len(a.urls), 1)

    def test_public_cdn_redirect_keeps_encoded_query(self):
        s, a = session(
            [
                (302, {"Location": "https://cdn.example.org/cover?q=%E4%B9%A6"}, b""),
                (200, {}, b"cover"),
            ]
        )
        with s:
            r = safe_network.get("https://example.com/search", params={"kw": "书名"}, session=s)
        self.assertEqual(r.content, b"cover")
        self.assertIn("kw=%E4%B9%A6%E5%90%8D", a.urls[0])
        self.assertEqual(a.urls[1], "https://cdn.example.org/cover?q=%E4%B9%A6")

    def test_stream_limits_even_without_length(self):
        s, a = session([(200, {}, b"x" * 101)])
        with s, self.assertRaises(ValueError):
            safe_network.get("https://example.com/", session=s, max_bytes=100)
        s, a = session([(200, {"Content-Length": "999"}, b"")])
        with s, self.assertRaises(ValueError):
            safe_network.get("https://example.com/", session=s, max_bytes=100)

    def test_redirect_loop_is_bounded(self):
        s, a = session([(302, {"Location": "/again"}, b"")] * 5)
        with s, self.assertRaises(ValueError):
            safe_network.get("https://example.com/", session=s)
        self.assertEqual(len(a.urls), 5)

    def test_direct_connection_pins_checked_public_ip(self):
        rows = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        conn = safe_network.PublicHTTPSConnection("example.com", timeout=3)
        with (
            patch.object(socket, "getaddrinfo", return_value=rows) as dns,
            patch.object(safe_network, "create_connection", return_value="socket") as connect,
        ):
            self.assertEqual(conn._new_conn(), "socket")
        self.assertEqual(dns.call_count, 1)
        self.assertEqual(connect.call_args.args[0], ("93.184.216.34", 443))
        self.assertEqual(conn.host, "example.com")

    def test_nonpublic_dns_never_connects(self):
        for address in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "198.18.0.1", "::1"):
            rows = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]
            with (
                patch.object(socket, "getaddrinfo", return_value=rows),
                patch.object(safe_network, "create_connection") as connect,
            ):
                with self.assertRaises(ValueError):
                    safe_network.PublicHTTPSConnection("example.com")._new_conn()
                connect.assert_not_called()

    def test_own_environment_proxy_is_honored(self):
        with (
            safe_network.PublicSession() as client,
            patch.dict(
                os.environ,
                {"https_proxy": "http://127.0.0.1:12345", "NO_PROXY": "", "no_proxy": ""},
            ),
        ):
            settings = client.merge_environment_settings(
                "https://example.com/", {}, False, None, None
            )
            self.assertEqual(settings["proxies"]["https"], "http://127.0.0.1:12345")
            adapter = client.get_adapter("https://example.com/")
            proxy = adapter.proxy_manager_for(settings["proxies"]["https"])
            self.assertIsNot(proxy.pool_classes_by_scheme["https"], safe_network.PublicHTTPSPool)

    def test_unexpected_decoder_not_called(self):
        from PIL import TiffImagePlugin

        raw = io.BytesIO()
        Image.new("RGB", (10, 10)).save(raw, "TIFF")
        raw.seek(0)
        with patch.object(
            TiffImagePlugin.TiffImageFile,
            "_open",
            side_effect=AssertionError("TIFF decoder invoked"),
        ) as decoder:
            with self.assertRaises(UnidentifiedImageError):
                open_image(raw)
            decoder.assert_not_called()

    def test_local_bmp_and_network_formats_still_work(self):
        for fmt in ("JPEG", "PNG", "WEBP", "BMP"):
            raw = io.BytesIO()
            Image.new("RGB", (100, 150), "blue").save(raw, fmt)
            raw.seek(0)
            with open_image(raw) as image:
                image.load()
                self.assertEqual(image.size, (100, 150))

    def test_custom_regex_backtracking_is_bounded(self):
        regex = HeadingPattern("(a|aa)+$")
        start = time.monotonic()
        with self.assertRaisesRegex(ValueError, "超时"):
            regex.fullmatch("a" * 60 + "!")
        self.assertLess(time.monotonic() - start, 1)
        self.assertTrue(HeadingPattern(r"第[一二三0-9]+章(?: .+)?").fullmatch("第一章 开始"))
        self.assertTrue(HeadingPattern(r"(章)\1").fullmatch("章章"))

    def test_docx_limits_apply_to_styles_and_notes_in_preview(self):
        for part, size in [
            ("word/styles.xml", 4_000_001),
            ("word/footnotes.xml", 16_000_001),
            ("word/endnotes.xml", 16_000_001),
        ]:
            with tempfile.TemporaryDirectory() as folder:
                p = Path(folder) / "bomb.docx"
                with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
                    z.writestr("word/document.xml", "<document/>")
                    z.writestr(part, b" " * size)
                with self.assertRaisesRegex(ValueError, "解压后过大"):
                    read_document(p, limit=16000)

    def test_pdf_image_first_page_produces_explicit_warning(self):
        import pymupdf

        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "mixed.pdf"
            raw = io.BytesIO()
            Image.new("RGB", (100, 150)).save(raw, "PNG")
            with pymupdf.open() as pdf:
                page = pdf.new_page()
                page.insert_image(page.rect, stream=raw.getvalue())
                page = pdf.new_page()
                page.insert_text((50, 100), "Chapter 1. Visible body remains.")
                pdf.save(p)
            result = read_document(p)
            self.assertIn("Visible body remains.", result.text)
            self.assertTrue(
                any("第 1 页" in warning and "OCR" in warning for warning in result.warnings)
            )

    def test_browser_retention_does_not_remove_live_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            browser = root / "archive/browser-search"
            profile = browser / "profile"
            profile.mkdir(parents=True)
            file = browser / ("a" * 64 + ".html")
            file.write_text("old search")
            (profile / "History").write_text("old")
            old = time.time() - 8 * DAY
            os.utime(file, (old, old))
            os.utime(profile, (old, old))
            recycled = []

            def recycle(paths):
                for p in paths:
                    recycled.append(p)
                    target = root / "retained" / str(len(recycled))
                    target.parent.mkdir(exist_ok=True)
                    shutil.move(str(p), target)

            active = CacheMaintenance(root, recycle=recycle)
            active.protect_resource(browser)
            sweeper = CacheMaintenance(root, recycle=recycle)
            sweeper.sweep()
            self.assertTrue(file.exists())
            self.assertTrue(profile.exists())
            sweeper.finish()
            active.finish()
            self.assertFalse(file.exists())
            self.assertFalse(profile.exists())

    def test_browser_rejects_arbitrary_navigation(self):
        with self.assertRaises(ValueError):
            cover_browser.render("https://example.com/")


class CoverPreferenceMigrationTests(unittest.TestCase):
    import test_bulk_workflows as fixtures

    tearDown = fixtures.BulkWorkflowTests.tearDown

    def setUp(self):
        import app

        with patch.object(app.ZBook, "restore", lambda ui: ui.settings.update(auto_cover=False)):
            self.fixtures.BulkWorkflowTests.setUp(self)

    def test_explicit_off_survives_old_profile_migration_and_save(self):
        import json

        self.assertFalse(self.widget.auto_cover.get())
        self.widget.persist()
        settings = json.loads((self.folder / "data/session.json").read_text(encoding="utf-8"))[
            "settings"
        ]
        self.assertFalse(settings["auto_cover"])


if __name__ == "__main__":
    unittest.main()
