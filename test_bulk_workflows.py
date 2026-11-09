# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json
import tempfile
import gc
import threading
import time
import tkinter as tk
import unittest
from PIL import Image
import app
import batch_limits
import preparation
from batch import Options,run_batch
from core import BookJob
from covers import Cover,SearchResult,first_matching_cover


class LimitTests(unittest.TestCase):
    def test_scanner_stops_after_one_extra_book_in_a_9000_book_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);paths=[]
            for number in range(51):
                path=root/f'{number}.txt';path.write_text('第一章\n正文',encoding='utf-8');paths.append(path)
            visited=[]
            def stream(*args):
                for number in range(9000):
                    visited.append(number)
                    yield paths[number] if number<51 else root/f'{number}.txt'
            with patch.object(batch_limits,'_sources',stream):result=batch_limits.collect_sources([root])
        self.assertEqual(result.paths,[])
        self.assertTrue(result.rejected)
        self.assertTrue(result.truncated)
        self.assertEqual(len(visited),51)

    def test_existing_queue_and_duplicate_paths_share_the_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            paths=[]
            for number in range(55):
                path=root/f'{number}.txt';path.write_text('正文',encoding='utf-8');paths.append(path)
            result=batch_limits.collect_sources(paths+paths,known=paths[:45])
        self.assertEqual(result.paths,[])
        self.assertTrue(result.rejected)
        self.assertTrue(result.truncated)
        self.assertEqual(result.duplicates,45)

    def test_file_and_total_byte_limits_skip_expensive_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);paths=[]
            for name,size in [('big.txt',7),('a.txt',5),('b.txt',5),('c.txt',5)]:
                path=root/name;path.write_bytes(b'x'*size);paths.append(path)
            with patch.object(batch_limits,'MAX_FILE_BYTES',6),patch.object(batch_limits,'MAX_BATCH_BYTES',12):
                result=batch_limits.collect_sources(paths)
        self.assertEqual([path.name for path in result.paths],['a.txt','b.txt'])
        self.assertEqual(result.oversized,1)
        self.assertTrue(result.byte_limited)

    def test_encoder_rejects_more_than_50_even_without_gui(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError,'最多转换 50 本'):
                run_batch([BookJob('missing.txt',str(number)) for number in range(51)],Options(directory,('epub',)))

    def test_cancelled_scan_does_not_read_files(self):
        cancel=threading.Event();cancel.set()
        result=batch_limits.collect_sources(['not-read'],cancel=cancel)
        self.assertEqual(result.paths,[])
        self.assertTrue(result.stopped)


class BulkWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.gc_enabled=gc.isenabled();gc.disable()
        self.directory=tempfile.TemporaryDirectory();self.folder=Path(self.directory.name)
        self.data_patch=patch.object(app,'DATA',self.folder/'data');self.root_patch=patch.object(app,'ROOT',self.folder)
        self.data_patch.start();self.root_patch.start()
        self.root=tk.Tk();self.root.withdraw();self.widget=app.ZBook(self.root)
        self.sources=[]
        for number in range(3):
            path=self.folder/f'《测试小说{number}》作者：作者{number}.txt'
            path.write_text('第一章 初见\n正文。\n第二章 尾声\n末段。',encoding='utf-8');self.sources.append(path)
        self.cover=self.folder/'matched.png';Image.new('RGB',(120,160),'navy').save(self.cover)

    def tearDown(self):
        self.widget.cancel.set();self.widget.analysis_shutdown.set()
        if self.widget.worker:self.widget.worker.join(timeout=3)
        self.widget.analysis_pool.shutdown(wait=True,cancel_futures=True)
        for identifier in self.root.tk.call('after','info'):self.root.after_cancel(identifier)
        self.root.destroy();self.root_patch.stop();self.data_patch.stop();self.directory.cleanup()
        self.widget=None;self.root=None;gc.collect()
        if self.gc_enabled:gc.enable()

    def wait(self,predicate,timeout=8):
        end=time.monotonic()+timeout
        while predicate() and time.monotonic()<end:self.root.update();time.sleep(.01)
        self.root.update();self.assertFalse(predicate(),'asynchronous operation timed out')

    def import_books(self):
        self.widget.add_paths(self.sources);self.wait(lambda:self.widget.busy or bool(self.widget.preparing))
        self.assertEqual(len(self.widget.jobs),3)

    def row(self,title,author,**values):
        return Cover(str(self.cover),title,'https://example.org','https://example.org/cover.png',120,160,'测试站点',author,values.get('match','书名、作者匹配'))

    def test_new_window_ignores_legacy_queue_and_conversion_history(self):
        self.import_books();self.widget.logs.configure(state='normal');self.widget.logs.insert('end','旧转换记录')
        session=self.folder/'data/session.json'
        session.write_text(json.dumps({'jobs':[job.to_dict() for job in self.widget.jobs],'settings':{'output':str(self.folder/'chosen-output')}}),encoding='utf-8')
        other_root=tk.Toplevel(self.root);other_root.withdraw();other=app.ZBook(other_root)
        try:
            self.assertEqual(other.jobs,[]);self.assertEqual(other.title.get(),'')
            self.assertEqual(other.logs.get('1.0','end').strip(),'');self.assertEqual(other.report_path,'')
            self.assertEqual(other.output.get(),str(self.folder/'chosen-output'))
            self.assertEqual(json.loads(session.read_text(encoding='utf-8'))['jobs'],[])
        finally:
            other.analysis_shutdown.set();other.analysis_pool.shutdown(wait=True,cancel_futures=True);other_root.destroy()

    def test_import_returns_before_metadata_read_and_ui_keeps_ticking(self):
        gate=threading.Event();entered=threading.Event();original=app.new_job
        def delayed(path):entered.set();gate.wait(3);return original(path)
        try:
            with patch.object(app,'new_job',delayed):
                start=time.monotonic();self.widget.add_paths(self.sources)
                self.assertLess(time.monotonic()-start,.2);self.assertTrue(entered.wait(2))
                tick=[];self.root.after(20,lambda:tick.append(True))
                end=time.monotonic()+.15
                while time.monotonic()<end:self.root.update();time.sleep(.005)
                self.assertTrue(tick);self.assertTrue(self.widget.busy)
                gate.set();self.wait(lambda:self.widget.busy or bool(self.widget.preparing))
        finally:gate.set()

    def test_one_click_prepares_unchecked_books_and_uses_verified_first_match(self):
        self.import_books()
        for job in self.widget.jobs:job.selected=False
        calls=[]
        def search(title,author,**kwargs):
            calls.append(title)
            return SearchResult([self.row(title,author,match='图片候选，作者待核对'),self.row(title,author)])
        with patch.object(preparation,'search_details',search):
            self.widget.prepare_all_button.command();self.wait(lambda:self.widget.busy)
        self.assertEqual(len(calls),3)
        self.assertTrue(all(job.cover_path==str(self.cover) for job in self.widget.jobs))
        self.assertTrue(all(job.chapter_count==2 for job in self.widget.jobs))
        self.assertFalse(any('作者请核对' in note for job in self.widget.jobs for note in job.notes))
        self.assertIn('封面 3/3',self.widget.status.get())

    def test_preparation_preview_follows_each_book_without_changing_checks(self):
        self.import_books()
        jobs=self.widget.jobs
        for index,job in enumerate(jobs):job.selected=index==1
        self.widget.refresh_queue()
        self.widget.tree.selection_set(jobs[-1].id);self.widget.on_select()
        self.widget.workspace_tabs.select(1)
        gates=[threading.Event() for _ in jobs];entered=[threading.Event() for _ in jobs]
        def search(title,author,**kwargs):
            index=next(i for i,job in enumerate(jobs) if job.title==title)
            entered[index].set()
            if not gates[index].wait(8):raise TimeoutError('preview inspection timed out')
            return SearchResult([self.row(title,author)])
        painted=[];preview=self.widget.preview_cover
        def record_cover():
            job=self.widget.current_job()
            if job and job.cover_path:painted.append(job.id)
            preview()
        try:
            with patch.object(preparation,'search_details',search),patch.object(self.widget,'preview_cover',record_cover):
                self.widget.prepare_all()
                for index,job in enumerate(jobs):
                    self.wait(lambda:not entered[index].is_set() or self.widget.current!=job.id)
                    self.wait(lambda:self.widget.parsed is None)
                    self.assertEqual(self.widget.title.get(),job.title)
                    self.assertEqual(self.widget.author.get(),job.author)
                    self.assertEqual(self.widget.tree.focus(),job.id)
                    self.assertEqual(self.widget.workspace_tabs.selected,0)
                    self.assertEqual(len(self.widget.parsed.chapters),2)
                    self.assertIn(f'{index+1}/3',self.widget.queue_notice.get())
                    gates[index].set()
                self.wait(lambda:self.widget.busy)
        finally:
            for gate in gates:gate.set()
        self.assertEqual(painted,[job.id for job in jobs])
        self.assertEqual(self.widget.current,jobs[-1].id)
        self.assertEqual([job.selected for job in jobs],[False,True,False])
        # Events left in the queue after stopping must not jump the preview.
        self.widget.follow_preparation(jobs[0].id,1,3)
        self.assertEqual(self.widget.current,jobs[-1].id)

    def test_one_failed_search_does_not_stop_other_books(self):
        self.import_books()
        def search(title,author,**kwargs):
            if title.endswith('1'):raise ConnectionError('测试网络失败')
            return SearchResult([self.row(title,author)])
        with patch.object(preparation,'search_details',search):
            self.widget.prepare_all();self.wait(lambda:self.widget.busy)
        self.assertEqual(sum(bool(job.cover_path) for job in self.widget.jobs),2)
        self.assertIn('1 本待补封面',self.widget.status.get())

    def test_cancellation_stops_before_next_book_and_unlocks_controls(self):
        self.import_books();gate=threading.Event();entered=threading.Event();calls=[]
        def search(title,author,**kwargs):
            calls.append(title);entered.set();gate.wait(3);return SearchResult([self.row(title,author)])
        try:
            with patch.object(preparation,'search_details',search):
                self.widget.prepare_all();self.assertTrue(entered.wait(2));self.widget.stop();gate.set()
                self.wait(lambda:self.widget.busy)
        finally:gate.set()
        self.assertEqual(len(calls),1);self.assertTrue(self.widget.cancel.is_set())
        self.assertFalse(any(job.cover_path for job in self.widget.jobs))
        self.assertIn('已停止',self.widget.status.get())

    def test_existing_manual_cover_is_preserved_without_network_request(self):
        self.import_books()
        for job in self.widget.jobs:job.cover_path=str(self.cover)
        with patch.object(preparation,'search_details') as network:
            self.widget.prepare_all();self.wait(lambda:self.widget.busy)
        network.assert_not_called();self.assertIn('封面 3/3',self.widget.status.get())

    def test_preferences_retry_does_not_block_gui_or_save_the_queue(self):
        original=Path.replace;attempts=[]
        def transient_lock(source,target):
            attempts.append(str(source))
            if len(attempts)==1:raise PermissionError('temporary file lock')
            return original(source,target)
        self.widget.output.set(str(self.folder/'new-output'))
        with patch.object(Path,'replace',autospec=True,side_effect=transient_lock):
            started=time.monotonic();self.widget.persist()
            self.assertLess(time.monotonic()-started,.1)
            self.assertIsNotNone(self.widget.preferences_retry)
            self.wait(lambda:self.widget.preferences_retry is not None)
        saved=json.loads((self.folder/'data/session.json').read_text(encoding='utf-8'))
        self.assertEqual(len(attempts),2)
        self.assertEqual(saved['jobs'],[])
        self.assertEqual(saved['settings']['output'],str(self.folder/'new-output'))

    def test_folder_over_limit_rejects_without_partially_loading_files(self):
        with patch.object(app,'MAX_BATCH_FILES',2),patch.object(batch_limits,'MAX_BATCH_FILES',2):
            with patch.object(app.messagebox,'showwarning') as warning:
                self.widget.add_paths([self.folder]);self.wait(lambda:self.widget.busy or bool(self.widget.preparing))
            self.assertEqual(self.widget.jobs,[]);warning.assert_called_once()
            self.assertIn('无法导入',self.widget.status.get())
            self.widget.add_paths(self.sources[:2]);self.wait(lambda:self.widget.busy or bool(self.widget.preparing))
            self.assertEqual(len(self.widget.jobs),2)

    def test_first_matching_cover_rejects_wrong_author_and_unrelated_title(self):
        candidates=[self.row('同名小说','其他作者'),self.row('另一本小说','正确作者'),self.row('同名小说','正确作者',match='图片候选，作者待核对'),self.row('同名小说','正确作者')]
        self.assertIs(first_matching_cover(candidates,'同名小说','正确作者'),candidates[-1])


if __name__=='__main__':unittest.main(verbosity=2)
