# SPDX-License-Identifier: AGPL-3.0-only
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import json
import tempfile
import time
import tkinter as tk
import unittest
import zipfile
from PIL import Image,ImageFont
import app
import cover_design
from core import Metadata,parse_text
from epub_export import write_epub
import test_bulk_workflows as fixtures


class CoverCompatibilityTests(unittest.TestCase):
    def test_color_styles_keep_grayscale_contrast_and_migrate_old_preferences(self):
        from PIL import ImageColor
        for style,colors in cover_design.PALETTES.items():
            raw=cover_design.generated_cover('星际旅人','青杉',style=style)
            with Image.open(BytesIO(raw)) as image:
                # A visible amount of color remains at a reader thumbnail size.
                thumb=image.resize((160,240))
                self.assertGreater(sum(max(pixel)-min(pixel)>20 for pixel in thumb.getdata()),160*240*.1)
                gray=image.convert('L')
                self.assertGreater(gray.getpixel((500,100))-gray.getpixel((10,100)),90)
                self.assertGreater(gray.getpixel((90,1000))-gray.getpixel((220,1200)),90)
                def luminance(color):
                    return Image.new('RGB',(1,1),ImageColor.getrgb(color)).convert('L').getpixel((0,0))
                self.assertGreater(luminance(colors['paper'])-luminance(colors['text']),180)
                self.assertGreater(luminance(colors['paper'])-luminance(colors['author']),145)
        self.assertEqual(cover_design.normalize_cover_style('ink'),'modern')
        self.assertEqual(cover_design.generated_cover('书名','作者',style='ink'),cover_design.generated_cover('书名','作者'))

    def test_builtin_font_needs_no_system_font_files(self):
        cover_design.font.cache_clear();cover_design.generated_cover.cache_clear()
        original=ImageFont.truetype
        with patch.object(ImageFont,'truetype',wraps=original) as loading:
            raw=cover_design.generated_cover('繁體與简体中文小说','青杉')
        self.assertTrue(loading.call_count)
        self.assertTrue(all(isinstance(call.args[0],BytesIO) for call in loading.call_args_list))
        with Image.open(BytesIO(raw)) as image:
            image.load();self.assertEqual((image.format,image.mode,image.size),('JPEG','RGB',(1000,1500)))

    def test_long_titles_and_authors_stay_inside_cover_text_area(self):
        title='一个很长的中文书名与 English Words：'+('穿越世界后的现代生活与魔法故事'*30)
        author='作者甲乙丙丁戊己庚辛壬癸'*20
        layout=cover_design.text_layout(title,author)
        self.assertLessEqual(len(layout['title']),9)
        self.assertLessEqual(layout['title_top']+len(layout['title'])*layout['line_step'],900)
        face=cover_design.font(layout['size'])
        self.assertTrue(all(face.getlength(line)<=810 for line in layout['title']))
        raw=cover_design.generated_cover(title,author)
        with Image.open(BytesIO(raw)) as image:image.verify()

    def test_unicode_punctuation_and_placeholder_generate_stable_jpeg(self):
        for title,author in [('A & B <小说> “引号”','作者 / Author'),('繁體小說：星際旅人','青杉'),('𠮷与表情😀的故事','测试作者'),('','')]:
            first=cover_design.generated_cover(title,author)
            cover_design.generated_cover.cache_clear()
            self.assertEqual(first,cover_design.generated_cover(title,author))
            with Image.open(BytesIO(first)) as image:self.assertEqual(image.mode,'RGB');image.verify()
        self.assertNotEqual(cover_design.generated_cover('','',True),cover_design.generated_cover('',''))

    def test_cover_image_and_full_unicode_metadata_are_embedded_in_epub(self):
        title='繁體 & English <书名> '+('很长的书名'*30);author='作者 & Author'
        book=parse_text('第一章 起点\n正文完整。',Metadata(title,author))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'book.epub';write_epub(book,path)
            with zipfile.ZipFile(path) as package:
                cover=next(name for name in package.namelist() if name.endswith('cover.jpg'))
                with Image.open(BytesIO(package.read(cover))) as image:self.assertEqual((image.mode,image.size),('RGB',(1000,1500)))
                from xml.etree import ElementTree as ET
                opf=ET.fromstring(package.read('OEBPS/content.opf'))
                ns={'dc':'http://purl.org/dc/elements/1.1/'}
                self.assertEqual(opf.find('.//dc:title',ns).text,title)
                self.assertEqual(opf.find('.//dc:creator',ns).text,author)


class ModernWorkspaceTests(unittest.TestCase):
    setUp=fixtures.BulkWorkflowTests.setUp
    tearDown=fixtures.BulkWorkflowTests.tearDown
    wait=fixtures.BulkWorkflowTests.wait
    import_books=fixtures.BulkWorkflowTests.import_books

    def realize_layout(self,width=1100,height=780):
        scale=self.widget.ui_scale
        self.root.geometry(f'{int(width*scale)}x{int(height*scale)}+30000+30000')
        self.root.deiconify()
        end=time.monotonic()+.2
        while time.monotonic()<end:self.root.update();time.sleep(.01)

    def test_right_side_workspace_and_three_separate_queue_actions(self):
        self.realize_layout();widget=self.widget
        self.assertGreater(widget.tree.winfo_rootx(),widget.body.winfo_rootx())
        self.assertLess(widget.chapter_tree.winfo_rootx(),widget.body.winfo_rootx())
        self.assertGreater(widget.chapter_tree.winfo_width(),widget.body.winfo_width())
        self.assertEqual([button.label for button in (widget.select_all_button,widget.select_none_button,widget.remove_selected_button)],['全选','取消全选','移除选中'])
        self.assertEqual(len(widget.select_all_button.master.winfo_children()),3)
        self.assertEqual(widget.import_files_button.master,widget.import_folder_button.master)
        self.assertLess(widget.import_files_button.winfo_rooty(),widget.tree.winfo_rooty())

    def test_mobi_is_default_and_legacy_combined_default_is_migrated(self):
        self.assertEqual(self.widget.format_choice.get(),'MOBI')
        session=self.folder/'data/session.json'
        session.write_text(json.dumps({'settings':{'epub':True,'mobi':True}}),encoding='utf-8')
        other_root=tk.Toplevel(self.root);other_root.withdraw();other=app.ZBook(other_root)
        try:
            self.assertEqual(other.format_choice.get(),'MOBI');self.assertFalse(other.epub.get());self.assertTrue(other.mobi.get())
        finally:other.analysis_shutdown.set();other.analysis_pool.shutdown(wait=True,cancel_futures=True);other_root.destroy()

    def test_more_than_50_selected_files_are_rejected_before_reading_metadata(self):
        with patch.object(app.messagebox,'showwarning') as warning,patch.object(app,'new_job') as reading:
            self.widget.add_paths([self.folder/f'{number}.txt' for number in range(51)])
        reading.assert_not_called();warning.assert_called_once()
        self.assertEqual(self.widget.jobs,[]);self.assertFalse(self.widget.busy)
        self.assertIn('无法导入',self.widget.status.get())

    def test_large_folder_is_rejected_as_a_whole_with_no_metadata_reads(self):
        source=self.folder/'large';source.mkdir()
        for number in range(51):(source/f'{number}.txt').write_text('第一章\n正文',encoding='utf-8')
        with patch.object(app.messagebox,'showwarning') as warning,patch.object(app,'new_job') as reading:
            self.widget.add_paths([source]);self.wait(lambda:self.widget.busy)
        reading.assert_not_called();warning.assert_called_once();self.assertEqual(self.widget.jobs,[])

    def test_multi_file_dialog_imports_all_selected_files(self):
        with patch.object(app.filedialog,'askopenfilenames',return_value=tuple(str(path) for path in self.sources)):
            self.widget.add_files();self.wait(lambda:self.widget.busy or bool(self.widget.preparing))
        self.assertEqual({job.path for job in self.widget.jobs},{str(path) for path in self.sources})

    def test_clipboard_action_explains_and_imports_an_image(self):
        self.import_books()
        self.assertEqual(self.widget.paste_cover_button.label,'粘贴封面')
        self.assertEqual(self.widget.paste_cover_tip.text,'从剪贴板粘贴图片')
        with patch.object(app.ImageGrab,'grabclipboard',return_value=Image.new('RGBA',(240,320),(30,80,160,180))):
            self.widget.paste_cover_button.invoke()
        job=self.widget.current_job();self.assertTrue(Path(job.cover_path).is_file())
        self.assertIn('剪贴板',job.cover_source)

    def test_minimum_window_keeps_chapter_scrollbar_and_actions_visible(self):
        widget=self.widget;scale=widget.ui_scale
        self.realize_layout(1020,720)
        edge=widget.metadata_surface.winfo_rootx()+widget.metadata_surface.winfo_width()
        self.assertLessEqual(widget.text_cover_button.winfo_rootx()+widget.text_cover_button.winfo_width(),edge-int(8*scale))
        edge=widget.preview_surface.winfo_rootx()+widget.preview_surface.winfo_width()
        self.assertLessEqual(widget.chapter_scroll.winfo_rootx()+widget.chapter_scroll.winfo_width(),edge-int(8*scale))
        self.assertGreater(widget.body.winfo_width(),int(175*scale))
        self.assertGreater(widget.chapter_tree.winfo_width(),int(140*scale))


if __name__=='__main__':unittest.main(verbosity=2)
