# SPDX-License-Identifier: AGPL-3.0-only
"""Reading-order and default typography regressions."""

import unittest, zipfile, json
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET
from core import parse_text, Metadata
from epub_export import write_epub
from batch import Options
from reading_style import migrate_settings, LINE_HEIGHT, PARAGRAPH_GAP

N = {
    "o": "http://www.idpf.org/2007/opf",
    "x": "http://www.w3.org/1999/xhtml",
    "epub": "http://www.idpf.org/2007/ops",
}


class ReadingLayoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="zbook-reading-tests-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)

    def test_real_world_chapter_boundaries(self):
        book = parse_text(
            "版权信息\n内容简介：\n简介文字。\n第一集 第1章 初临起源大陆\n起点正文。\n第2章 继续。\n后续正文。",
            Metadata("书名"),
        )
        self.assertEqual(
            [ch.kind for ch in book.chapters], ["intro", "intro", "volume", "chapter", "chapter"]
        )
        self.assertEqual(book.chapters[-1].title, "第2章 继续。")
        repeated = parse_text(
            "第2032章 永恒（完）\n　　第2032章 永恒（完）“成为神上神，挺无聊的.”\n完整正文。",
            Metadata("书名"),
        )
        self.assertEqual(len(repeated.chapters), 1)
        self.assertIn("成为神上神", repeated.chapters[0].text)
        path = self.work / "combined-heading.epub"
        write_epub(book, path)
        with zipfile.ZipFile(path) as z:
            opf = ET.fromstring(z.read("OEBPS/content.opf"))
            start = opf.find('o:guide/o:reference[@type="text"]', N).get("href")
            self.assertIn(
                "第1章 初临起源大陆", "".join(ET.fromstring(z.read("OEBPS/" + start)).itertext())
            )
            self.assertIn("第一集", z.read("OEBPS/nav.xhtml").decode("utf-8"))

    def test_toc_is_not_a_reading_page(self):
        book = parse_text(
            "版权前言。\n第一卷 初始\n第一章 起点\n正文甲。\n第二章 后续\n正文乙。",
            Metadata("阅读顺序", "作者"),
        )
        path = self.work / "reading-order.epub"
        write_epub(book, path)
        with zipfile.ZipFile(path) as z:
            opf = ET.fromstring(z.read("OEBPS/content.opf"))
            spine = [e.get("idref") for e in opf.findall("o:spine/o:itemref", N)]
            self.assertNotIn("nav", spine)
            self.assertEqual(spine[0], "cover-page")
            self.assertTrue(all(x.startswith("ch") for x in spine[1:]))
            self.assertTrue(opf.findall('o:manifest/o:item[@properties="nav"]', N))
            refs = opf.findall("o:guide/o:reference", N)
            self.assertFalse(any(e.get("type") == "toc" for e in refs))
            start = next(e.get("href") for e in refs if e.get("type") == "text")
            content = "".join(ET.fromstring(z.read("OEBPS/" + start)).itertext())
            self.assertIn("第一章 起点", content)
            nav = ET.fromstring(z.read("OEBPS/nav.xhtml"))
            self.assertIn("第二章 后续", "".join(nav.itertext()))
            css = z.read("OEBPS/style.css").decode("utf8")
            for value in [
                "font-size: 1em",
                "font-size: 1.2em",
                "text-align: left",
                "line-height: 1.2",
                "margin: 0em auto",
                "margin: 0 5pt",
                "text-indent: 2em",
            ]:
                self.assertIn(value, css)
            self.assertNotIn("page-break-before: always", css)

    def test_custom_spacing_still_works(self):
        path = self.work / "custom-spacing.epub"
        write_epub(
            parse_text("第一章 一\n正文", Metadata("测试", "")),
            path,
            line_height=1.8,
            paragraph_gap=0.3,
        )
        with zipfile.ZipFile(path) as z:
            css = z.read("OEBPS/style.css").decode("utf8")
            self.assertIn("line-height: 1.8", css)
            self.assertIn("margin: 0.3em auto", css)

    def test_shared_defaults_and_session_migration(self):
        self.assertEqual(Options().line_height, LINE_HEIGHT)
        self.assertEqual(Options().paragraph_gap, PARAGRAPH_GAP)
        old = {"line_height": "1.65", "paragraph_gap": "0.5", "output": "用户路径"}
        migrate_settings(old)
        self.assertEqual(old["line_height"], "1.2")
        self.assertEqual(old["paragraph_gap"], "0")
        self.assertEqual(old["output"], "用户路径")
        custom = {"line_height": "1.8", "paragraph_gap": "0.3"}
        migrate_settings(custom)
        self.assertEqual(custom["line_height"], "1.8")
        self.assertEqual(custom["paragraph_gap"], "0.3")
        custom["line_height"] = "1.65"
        migrate_settings(custom)
        self.assertEqual(custom["line_height"], "1.65")


if __name__ == "__main__":
    unittest.main()
