# SPDX-License-Identifier: AGPL-3.0-only
from types import SimpleNamespace
from unittest.mock import patch
import threading
import time
import tkinter as tk
import unittest

import app
from core import Metadata,parse_text,heading_kind
from layout_widgets import SmoothScrollCanvas
import test_bulk_workflows as fixtures


class VolumeRuleTests(unittest.TestCase):
    def test_prose_words_notes_and_quoted_references_remain_body(self):
        samples=[
            '第一部族和小聚集地人的差距，完全是天与地之间的差别。',
            '第三部族的信仰，顺利收归囊中。',
            '第一部分，是由首相大人拜托智慧神教教宗做的机械。',
            '第二部分就是这个系统。',
            '第二卷就写妻妾离心，功败垂成的故事吧！',
            '第一卷小卖，第二卷腰斩。',
            '第三集非常积极，第四集就来一个游子的味道……',
            '第一卷结束','第三卷（完）','第三卷22章修改说明——',
            '第二卷 创作总结','第二卷第十一节，作者卡斯托尔，我相信其他几本魔典里也有过类似的观点。”',
        ]
        for text in samples:
            with self.subTest(text=text):
                self.assertIsNone(heading_kind(text,'自动（中文 / 英文）'))
                book=parse_text('第一章 起点\n正文。\n'+text+'\n接下来的正文。',Metadata('测试书'))
                self.assertFalse(any(chapter.kind=='volume' for chapter in book.chapters))
                self.assertIn(text,'\n'.join(chapter.text for chapter in book.chapters))

    def test_real_titles_and_combined_headings_keep_restarting_chapter_numbers(self):
        text='第一卷第一章 启程\n正文甲。\n第二卷 第二章 归来\n正文乙。\n第三卷 第一章 再启\n正文丙。'
        book=parse_text(text,Metadata('测试书'))
        self.assertEqual([chapter.title for chapter in book.chapters if chapter.kind=='volume'],['第一卷','第二卷','第三卷'])
        self.assertEqual([chapter.title for chapter in book.chapters if chapter.kind=='chapter'],['第一章 启程','第二章 归来','第一章 再启'])
        same=parse_text('第一卷第一章 起点\n正文甲。\n第二卷第一章 新起点\n正文乙。',Metadata('测试书'))
        self.assertEqual(sum(chapter.kind=='chapter' for chapter in same.chapters),2)
        self.assertEqual(sum(chapter.kind=='volume' for chapter in same.chapters),2)

    def test_title_series_allows_long_previews_before_chapters(self):
        preview='\n'.join('　　这是卷前预告，不是正文中的卷名。' for _ in range(18))
        book=parse_text('第一集 细雨\n'+preview+'\n第一章 初见\n正文。\n\n第二集 长夜\n'+preview+'\n第二章 重逢\n正文。',Metadata('测试书'))
        self.assertEqual([chapter.title for chapter in book.chapters if chapter.kind=='volume'],['第一集 细雨','第二集 长夜'])

    def test_english_titles_and_explicit_document_heading_remain_supported(self):
        book=parse_text('Volume I The Start\nChapter 1 Departure\nBody.\nVolume II Return\nChapter 2 Home\nBody.',Metadata('Book'))
        self.assertEqual(sum(chapter.kind=='volume' for chapter in book.chapters),2)
        document=parse_text('第一部分 绪论\n介绍文字。',Metadata('报告'),heading_levels={1:1})
        self.assertEqual(document.chapters[0].kind,'volume')


class ImportFollowTests(unittest.TestCase):
    setUp=fixtures.BulkWorkflowTests.setUp
    tearDown=fixtures.BulkWorkflowTests.tearDown
    wait=fixtures.BulkWorkflowTests.wait

    def test_directory_results_follow_each_imported_book(self):
        gates=[threading.Event() for _ in range(3)];entered=[threading.Event() for _ in range(3)]
        original=app.read_job
        def delayed(job):
            index=int(job.title[-1]);entered[index].set();gates[index].wait(8);return original(job)
        try:
            with patch.object(app,'read_job',delayed):
                self.widget.add_paths(self.sources)
                self.wait(lambda:len(self.widget.jobs)<3 or self.widget.importing)
                for index in range(3):
                    gates[index].set()
                    self.wait(lambda:self.widget.current!=self.widget.jobs[index].id or self.widget.jobs[index].id in self.widget.preparing)
                    job=self.widget.jobs[index]
                    self.assertEqual(self.widget.title.get(),job.title)
                    self.assertEqual(self.widget.source_name.get(),self.sources[index].name)
                    self.assertEqual(self.widget.tree.selection(),(job.id,))
                    self.assertEqual(len(self.widget.parsed.chapters),2)
                self.wait(lambda:bool(self.widget.preparing))
        finally:
            for gate in gates:gate.set()
        self.assertTrue(all(job.selected for job in self.widget.jobs))
        self.assertEqual(self.widget.progress_percent.get(),'0%')

    def test_failed_import_analysis_shows_error_without_recursive_retry(self):
        calls=[]
        def fail(job):calls.append(job.id);raise ValueError('模拟无法识别的正文')
        with patch.object(app,'read_job',fail):
            self.widget.add_paths(self.sources[:1]);self.wait(lambda:self.widget.busy or bool(self.widget.preparing))
        self.assertEqual(len(calls),1)
        self.assertEqual(self.widget.current,self.widget.jobs[0].id)
        self.assertIn('模拟无法识别的正文',self.widget.body.get('1.0','end'))


class SmoothAuxiliaryWindowTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.geometry('400x250+30000+30000')
        self.canvas=SmoothScrollCanvas(self.root,bg='white',highlightthickness=0)
        self.canvas.pack(fill='both',expand=True)
        self.canvas.create_rectangle(0,0,350,2400,fill='white');self.canvas.configure(scrollregion=(0,0,350,2400))
        self.root.update()
    def tearDown(self):self.root.destroy()
    def settle(self):
        end=time.monotonic()+.35
        while time.monotonic()<end:self.root.update();time.sleep(.01)
    def test_small_touchpad_deltas_move_by_pixels(self):
        self.canvas.wheel(SimpleNamespace(delta=-15));self.settle()
        position=self.canvas.yview()[0]*2400
        self.assertGreater(position,0);self.assertLess(position,30*self.canvas.scale)
        self.canvas.wheel(SimpleNamespace(delta=15));self.settle()
        self.assertLessEqual(self.canvas.yview()[0]*2400,1)
    def test_dragging_scrollbar_cancels_animation_and_window_closes_cleanly(self):
        self.canvas.wheel(SimpleNamespace(delta=-120));self.canvas.yview_moveto(.5)
        self.settle();self.assertAlmostEqual(self.canvas.yview()[0],.5,places=2)
        self.canvas.wheel(SimpleNamespace(delta=-120));self.canvas.destroy();self.root.update()


if __name__=='__main__':unittest.main(verbosity=2)
