# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
from unittest.mock import patch
import json
import threading
import unittest
from PIL import Image
import app
import preparation
from covers import SearchResult
import test_bulk_workflows as fixtures


class CoverWorkspaceTests(unittest.TestCase):
    setUp=fixtures.BulkWorkflowTests.setUp
    tearDown=fixtures.BulkWorkflowTests.tearDown
    wait=fixtures.BulkWorkflowTests.wait
    import_books=fixtures.BulkWorkflowTests.import_books
    row=fixtures.BulkWorkflowTests.row

    def select(self,job):
        self.widget.tree.selection_set(job.id);self.widget.on_select()
        self.wait(lambda:self.widget.parsed is None)

    def test_selecting_evicted_books_uses_snapshot_without_source_parsing(self):
        self.import_books();jobs=self.widget.jobs
        versions=dict(self.widget.analysis_versions)
        self.widget.book_cache.clear()
        with patch.object(app,'read_job',side_effect=AssertionError('selection must not parse source')) as parser:
            for job in jobs+jobs[::-1]:
                self.select(job)
                self.assertEqual(self.widget.parsed.metadata.title,job.title)
                self.assertEqual(len(self.widget.parsed.chapters),2)
            self.widget.title.set('改过的书名');self.widget.author.set('新作者');self.widget.apply_current()
            self.widget.book_cache.clear();self.select(jobs[1]);self.select(jobs[0])
            self.assertEqual(self.widget.parsed.metadata.title,'改过的书名')
            self.assertEqual(self.widget.parsed.metadata.author,'新作者')
            parser.assert_not_called()
        self.assertEqual(self.widget.analysis_versions,versions)

    def test_cover_button_only_searches_and_lists_all_unmatched_books_at_end(self):
        self.import_books();jobs=self.widget.jobs;self.widget.book_cache.clear()
        def search(title,author,**kwargs):
            if title==jobs[1].title:return SearchResult([])
            if title==jobs[2].title:raise ConnectionError('测试网络错误')
            return SearchResult([self.row(title,author)])
        with patch.object(app,'read_job',side_effect=AssertionError('cover search must not parse')) as parser,patch.object(preparation,'search_details',search):
            self.widget.prepare_all_button.command();self.wait(lambda:self.widget.busy)
            parser.assert_not_called()
        text=self.widget.logs.get('1.0','end');tail=text[text.rfind('未完成封面：'):]
        self.assertIn('未完成封面：2 本',tail)
        self.assertIn(jobs[1].title,tail);self.assertIn(jobs[2].title,tail)
        self.assertNotIn(jobs[0].title,tail)
        self.assertIn('测试网络错误',tail)
        self.assertEqual(self.widget.workspace_tabs.selected,1)
        report=json.loads(Path(self.widget.report_path).read_text(encoding='utf-8'))
        self.assertEqual(len(report['missing_covers']),2)
        self.assertIn('未完成封面：2 本',Path(self.widget.progress_log).read_text(encoding='utf-8'))
        self.assertEqual(self.widget.queue_cover_label(jobs[0]),'已匹配图片')
        self.assertEqual(self.widget.queue_cover_label(jobs[1]),'未匹配 · 待补')
        self.assertEqual(self.widget.queue_cover_label(jobs[2]),'搜索失败')
        for job in jobs:self.assertTrue(self.widget.tree.item(job.id,'image'))
        self.root.update();self.widget.tree._draw()
        self.assertTrue(self.widget.tree.find_withtag('book-cover'))

    def test_manual_directory_refresh_reparses_all_books_without_searching_covers(self):
        self.import_books();versions=dict(self.widget.analysis_versions);original=app.read_job
        with patch.object(app,'read_job',wraps=original) as parser,patch.object(preparation,'search_details') as network:
            self.widget.organize_all_button.command();self.wait(lambda:self.widget.busy)
            self.assertEqual(parser.call_count,3);network.assert_not_called()
        self.assertTrue(all(self.widget.analysis_versions[job.id]>versions[job.id] for job in self.widget.jobs))
        self.assertTrue(all(job.chapter_count==2 for job in self.widget.jobs))
        self.assertIn('未完成目录：0 本',self.widget.logs.get('1.0','end'))

    def test_manual_cover_changes_update_thumbnail_and_state_immediately(self):
        self.import_books();job=self.widget.current_job();original=self.widget.queue_thumbnail(job)
        self.widget.assign_local_image(Image.new('RGB',(180,240),'#D15F39'),'测试手动封面')
        self.assertIsNot(self.widget.queue_thumbnail(job),original)
        self.assertEqual(self.widget.queue_cover_label(job),'已有图片')
        with patch.object(preparation,'search_details') as search:
            self.widget.prepare_all();self.wait(lambda:self.widget.busy)
        self.widget.clear_cover()
        self.assertEqual(self.widget.queue_cover_label(job),'指定文字封面')

    def test_stopped_search_final_summary_names_every_unprocessed_book(self):
        self.import_books();gate=threading.Event();entered=threading.Event();jobs=self.widget.jobs
        def search(title,author,**kwargs):
            entered.set();gate.wait(4);return SearchResult([self.row(title,author)])
        try:
            with patch.object(preparation,'search_details',search):
                self.widget.prepare_all();self.assertTrue(entered.wait(2));self.widget.stop();gate.set()
                self.wait(lambda:self.widget.busy)
        finally:gate.set()
        tail=self.widget.logs.get('1.0','end').split('未完成封面：')[-1]
        self.assertIn('3 本',tail)
        for job in jobs:self.assertIn(job.title,tail)
        self.assertIn('已停止',tail)

    def test_conversion_summary_names_fallback_books_and_omits_explicit_text_covers(self):
        self.import_books();jobs=self.widget.jobs
        self.widget.summarize_conversion_covers({'results':[
            {'title':jobs[0].title,'input':jobs[0].path,'cover_type':'图片封面'},
            {'title':jobs[1].title,'input':jobs[1].path,'cover_type':'文字封面','cover_source':'自动文字封面','warnings':['联网封面失败，使用文字封面：连接超时']},
            {'title':jobs[2].title,'input':jobs[2].path,'cover_type':'文字封面','cover_source':'指定文字封面'},
        ]})
        tail=self.widget.logs.get('1.0','end').split('未完成封面：')[-1]
        self.assertIn('1 本',tail);self.assertIn(jobs[1].title,tail);self.assertIn('连接超时',tail)
        self.assertNotIn(jobs[0].title,tail);self.assertNotIn(jobs[2].title,tail)

    def test_stopping_cover_search_does_not_cancel_separate_directory_analysis(self):
        self.import_books();job=self.widget.jobs[0]
        parse_gate=threading.Event();parse_entered=threading.Event();search_gate=threading.Event();search_entered=threading.Event()
        original=app.read_job
        def read(snapshot):
            parse_entered.set();parse_gate.wait(5);return original(snapshot)
        def search(title,author,**kwargs):
            search_entered.set();search_gate.wait(5);return SearchResult([])
        try:
            with patch.object(app,'read_job',read),patch.object(preparation,'search_details',search):
                self.widget.prepare_job(job);self.assertTrue(parse_entered.wait(2))
                self.widget.prepare_all();self.assertTrue(search_entered.wait(2));self.widget.stop()
                self.assertIn(job.id,self.widget.preparing)
                parse_gate.set();search_gate.set();self.wait(lambda:self.widget.busy or bool(self.widget.preparing))
        finally:parse_gate.set();search_gate.set()
        self.assertEqual(job.chapter_count,2)

    def test_damaged_preview_cache_prompts_manual_refresh_without_reparsing(self):
        self.import_books();job=self.widget.jobs[0]
        self.widget.book_cache.clear();self.widget.current=None
        target=self.widget.preview_store.path(job.id,self.widget.analysis_versions[job.id]);target.write_bytes(b'broken cache')
        with patch.object(app,'read_job') as parser:
            self.widget.tree.selection_set(job.id);self.widget.on_select()
            self.wait(lambda:self.widget.preview_info.get()!='预览缓存无法读取')
            parser.assert_not_called()
        self.assertIn('重新识别目录',self.widget.body.get('1.0','end'))


if __name__=='__main__':unittest.main(verbosity=2)
