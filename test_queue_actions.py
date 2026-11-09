# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
from unittest.mock import patch
import tempfile
import gc
import time
import tkinter as tk
import unittest
import app


class QueueActionTests(unittest.TestCase):
    def setUp(self):
        self.gc_enabled=gc.isenabled();gc.disable()
        self.directory=tempfile.TemporaryDirectory();self.folder=Path(self.directory.name)
        self.data_patch=patch.object(app,'DATA',self.folder/'data');self.root_patch=patch.object(app,'ROOT',self.folder)
        self.data_patch.start();self.root_patch.start()
        self.root=tk.Tk();self.root.withdraw();self.application=app.ZBook(self.root)
        self.sources=[]
        for number in range(3):
            path=self.folder/f'《测试小说{number}》作者：作者{number}.txt'
            path.write_text(f'第一章 开始\n正文{number}。\n第二章 结束\n末段。',encoding='utf-8')
            self.sources.append(path)
        self.application.add_paths(self.sources)
        end=time.monotonic()+10
        while (self.application.busy or self.application.preparing) and time.monotonic()<end:self.root.update();time.sleep(.02)
        self.root.update()

    def tearDown(self):
        self.application.cancel.set();self.application.analysis_shutdown.set()
        if self.application.worker:self.application.worker.join(timeout=3)
        self.application.analysis_pool.shutdown(wait=True,cancel_futures=True)
        for identifier in self.root.tk.call('after','info'):self.root.after_cancel(identifier)
        self.root.destroy();self.root_patch.stop();self.data_patch.stop();self.directory.cleanup()
        self.application=None;self.root=None;gc.collect()
        if self.gc_enabled:gc.enable()

    def test_all_none_toggle_and_remove_all_checked(self):
        widget=self.application
        self.assertEqual(widget.select_all_button.label,'全选')
        self.assertEqual(widget.select_none_button.label,'取消全选')
        widget.select_none_button.command()
        self.assertFalse(any(job.selected for job in widget.jobs))
        self.assertIn('已选 0',widget.summary.get())
        widget.select_all_button.command()
        self.assertTrue(all(job.selected for job in widget.jobs))
        self.assertIn('已选 3',widget.summary.get())
        widget.remove_selected_button.command();self.root.update()
        self.assertEqual(widget.jobs,[])
        self.assertFalse(widget.tree.get_children())
        self.assertEqual(widget.current,None)
        self.assertTrue(all(path.is_file() for path in self.sources))

    def test_partial_removal_preserves_current_unchecked_book(self):
        widget=self.application;middle=widget.jobs[1]
        widget.tree.selection_set(middle.id);self.root.update()
        middle.selected=False;widget.refresh_queue()
        removed={widget.jobs[0].id,widget.jobs[2].id}
        widget.remove_selected_button.command();self.root.update()
        self.assertEqual([job.id for job in widget.jobs],[middle.id])
        self.assertEqual(widget.current,middle.id)
        self.assertFalse(removed&set(widget.preparing))
        self.assertFalse(removed&set(widget.catalogs))
        self.assertFalse(removed&set(widget.analysis_futures))
        self.assertTrue(all(path.is_file() for path in self.sources))

    def test_remove_22_during_analysis_does_not_restore_removed_books(self):
        widget=self.application
        extra=[]
        for number in range(3,22):
            path=self.folder/f'《测试小说{number}》作者：作者{number}.txt'
            path.write_text(f'第一章 开始\n正文{number}。\n第二章 结束\n末段。',encoding='utf-8')
            extra.append(path)
        from threading import Event
        gate=Event();original=app.read_job
        def delayed(job):gate.wait(3);return original(job)
        self.addCleanup(gate.set)
        delayed_patch=patch.object(app,'read_job',delayed);delayed_patch.start();self.addCleanup(delayed_patch.stop)
        widget.add_paths(extra)
        end=time.monotonic()+4
        while widget.importing and time.monotonic()<end:self.root.update();time.sleep(.01)
        self.assertEqual(len(widget.jobs),22)
        self.assertTrue(widget.preparing)
        self.assertIn('已选 22',widget.summary.get())
        widget.remove_selected_button.command()
        gate.set()
        widget.analysis_pool.shutdown(wait=True,cancel_futures=True)
        end=time.monotonic()+.25
        while time.monotonic()<end:self.root.update();time.sleep(.01)
        self.assertEqual(widget.jobs,[])
        self.assertFalse(widget.tree.get_children())
        self.assertFalse(widget.preparing)
        self.assertFalse(widget.catalogs)
        self.assertFalse(widget.analysis_futures)
        self.assertFalse(widget.book_cache)
        self.assertTrue(all(path.is_file() for path in self.sources+extra))
        import json
        saved=json.loads((self.folder/'data/session.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['jobs'],[])

    def test_mixed_document_import_organizes_without_selecting_each_book(self):
        widget=self.application;fixtures=Path(__file__).resolve().parent/'tests/fixtures'
        widget.add_paths([fixtures/'word_headings.docx',fixtures/'text_novel.pdf',fixtures/'upstream_simple.doc'])
        end=time.monotonic()+12
        while (widget.busy or widget.preparing) and time.monotonic()<end:self.root.update();time.sleep(.02)
        self.assertFalse(widget.preparing)
        self.assertEqual(len(widget.jobs),6)
        self.assertTrue(all(job.chapter_count>0 for job in widget.jobs))
        self.assertTrue(all(job.status=='目录已就绪' for job in widget.jobs))


if __name__=='__main__':unittest.main(verbosity=2)
