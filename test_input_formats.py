# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
import json
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
import pymupdf
from core import new_job, read_job, parse_metadata
from input_sources import read_document, source_files
from epub_export import write_epub
from reading_style import migrate_settings, PROFILE

FIXTURES = Path(__file__).resolve().parent / "tests/fixtures"


class InputFormatTests(unittest.TestCase):
    def test_docx_metadata_headings_and_visible_text(self):
        job = new_job(FIXTURES / "word_headings.docx")
        book = read_job(job)
        self.assertEqual((job.title, job.author), ("冬夜物语", "青杉"))
        self.assertEqual(
            [
                (chapter.title, chapter.level)
                for chapter in book.chapters
                if chapter.kind == "chapter"
            ],
            [("开端", 1), ("林间小路", 2), ("归途", 1)],
        )
        source = read_document(job.path)
        self.assertNotIn("不应保留的修订", source.text)
        self.assertEqual(source.text.count("开端"), 1)
        self.assertIn("ZBook preserves English words and 中文。", source.text)
        self.assertIn("末段标记：所有正文应完整保留。", source.text)

    def test_explicit_filename_metadata_takes_priority(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "《用户书名》作者：用户作者.docx"
            source.write_bytes((FIXTURES / "word_headings.docx").read_bytes())
            job = new_job(source)
        self.assertEqual((job.title, job.author), ("用户书名", "用户作者"))

    def test_binary_word97_real_sample(self):
        doc = read_document(FIXTURES / "upstream_simple.doc")
        self.assertEqual(doc.text, "This is a simple file created with Word 97-SR2.")

    def test_binary_word_table_real_sample(self):
        doc = read_document(FIXTURES / "upstream_simple-table.doc")
        for cell in ("Cell 1,1", "Cell 1,2", "Cell 1,3", "Cell 2,1", "Cell 2,2", "Cell 2,3"):
            self.assertIn(cell, doc.text)
        self.assertTrue(doc.text.endswith("This text is below the table."))

    def test_rtf_word_document(self):
        job = new_job(FIXTURES / "legacy_rtf.doc")
        book = read_job(job)
        self.assertEqual((job.title, job.author), ("旧格式书简", "青杉"))
        self.assertIn("Text body.", book.chapters[-1].text)

    def test_pdf_reflows_lines_and_removes_margins(self):
        job = new_job(FIXTURES / "text_novel.pdf")
        book = read_job(job)
        self.assertEqual((job.title, job.author), ("长夜书简", "林舟"))
        source = read_document(job.path)
        self.assertNotIn("ZBook 测试页眉", source.text)
        self.assertNotIn("第 1 页", source.text)
        self.assertIn("这是同一段的第一行，续行应该合并，而不是生成另一个段落。", source.text)
        for number in range(1, 4):
            self.assertIn(f"页内正文编号{number}", source.text)
        self.assertEqual(sum(chapter.kind == "chapter" for chapter in book.chapters), 3)

    def test_scanned_pdf_requires_ocr(self):
        with self.assertRaisesRegex(ValueError, "OCR"):
            read_document(FIXTURES / "scanned.pdf")

    def test_encrypted_pdf_has_clear_failure(self):
        with self.assertRaisesRegex(ValueError, "已加密"):
            read_document(FIXTURES / "encrypted.pdf")

    def test_pdf_mixed_scan_does_not_silently_drop_pages(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "mixed.pdf"
            doc = pymupdf.open(FIXTURES / "text_novel.pdf")
            page = doc.new_page()
            page.insert_image(page.rect, filename=str(FIXTURES / "pdf_page_preview.png"))
            doc.save(path)
            doc.close()
            with self.assertRaisesRegex(ValueError, "只有图片"):
                read_document(path)

    def test_pdf_columns_follow_left_then_right(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "columns.pdf"
            doc = pymupdf.open()
            page = doc.new_page(width=595, height=842)
            page.insert_textbox(
                pymupdf.Rect(320, 100, 550, 500),
                "右栏开始。" + ("这是右栏正文，用来检查两栏的读取顺序。" * 4),
                fontname="china-s",
                fontsize=12,
            )
            page.insert_textbox(
                pymupdf.Rect(50, 100, 280, 500),
                "左栏开始。" + ("这是左栏正文，用来检查两栏的读取顺序。" * 4),
                fontname="china-s",
                fontsize=12,
            )
            doc.save(path)
            doc.close()
            source = read_document(path)
            self.assertLess(source.text.index("左栏开始"), source.text.index("右栏开始"))
            self.assertTrue(any("双栏" in warning for warning in source.warnings))

    def test_folder_import_supports_formats_and_skips_word_locks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "nested").mkdir()
            for name in (
                "book.TXT",
                "a.docx",
                "b.doc",
                "c.pdf",
                "d.rtf",
                "~$a.docx",
                "ignored.epub",
            ):
                (root / name).write_bytes(b"x")
            (root / "nested/e.pdf").write_bytes(b"x")
            paths = source_files([root, root / "a.docx"])
            self.assertEqual(len(paths), 6)
            self.assertFalse(any(path.name.startswith("~$") for path in paths))

    def test_document_epub_preserves_metadata_and_navigation(self):
        with tempfile.TemporaryDirectory() as folder:
            book = read_job(new_job(FIXTURES / "word_headings.docx"))
            target = Path(folder) / "book.epub"
            write_epub(book, target)
            with zipfile.ZipFile(target) as archive:
                metadata = ET.fromstring(archive.read("OEBPS/content.opf"))
                ns = {"dc": "http://purl.org/dc/elements/1.1/"}
                self.assertEqual(metadata.find(".//dc:title", ns).text, "冬夜物语")
                self.assertEqual(metadata.find(".//dc:creator", ns).text, "青杉")
                nav = archive.read("OEBPS/nav.xhtml").decode("utf-8")
                self.assertIn("林间小路", nav)
                all_text = "".join(
                    archive.read(name).decode("utf-8")
                    for name in archive.namelist()
                    if name.endswith(".xhtml")
                )
                self.assertIn("末段标记：所有正文应完整保留。", all_text)

    def test_existing_custom_reading_values_survive_brand_migration(self):
        settings = {
            "layout_profile": "previous-profile",
            "line_height": "1.65",
            "paragraph_gap": "0.5",
        }
        result = migrate_settings(settings)
        self.assertEqual((result["line_height"], result["paragraph_gap"]), ("1.65", "0.5"))
        self.assertEqual(result["layout_profile"], PROFILE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
