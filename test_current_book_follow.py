# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
from unittest.mock import patch
import threading
import unittest
import app
import test_bulk_workflows as fixtures


class ConversionFollowTests(unittest.TestCase):
    setUp=fixtures.BulkWorkflowTests.setUp
    tearDown=fixtures.BulkWorkflowTests.tearDown
    wait=fixtures.BulkWorkflowTests.wait
    import_books=fixtures.BulkWorkflowTests.import_books
    row=fixtures.BulkWorkflowTests.row
    def setup_conversion(self):
        self.import_books();self.widget.epub.set(True);self.widget.mobi.set(False)
        self.widget.auto_cover.set(True);self.widget.output.set(str(self.folder/'output'))
        self.widget.tree.selection_set(self.widget.jobs[-1].id);self.widget.on_select()
        self.widget.book_cache.clear()

    def test_conversion_scraping_follows_current_book_and_reuses_parsed_outline(self):
        self.setup_conversion();jobs=self.widget.jobs
        gates=[threading.Event() for _ in jobs];entered=[threading.Event() for _ in jobs]
        painted=[];preview=self.widget.preview_cover
        def record_cover():
            job=self.widget.current_job()
            if job and job.cover_path:painted.append(job.id)
            preview()
        def search(title,author,**kwargs):
            index=next(i for i,job in enumerate(jobs) if job.title==title)
            entered[index].set()
            if not gates[index].wait(8):raise TimeoutError('UI inspection timed out')
            return [self.row(title,author)]
        try:
            with patch('covers.search_covers',search),patch.object(app,'read_job') as extra_parse,patch.object(self.widget,'preview_cover',record_cover):
                self.widget.start()
                for index,job in enumerate(jobs):
                    self.wait(lambda:not entered[index].is_set() or self.widget.current!=job.id or self.widget.parsed is None)
                    self.assertEqual(self.widget.title.get(),job.title)
                    self.assertEqual(self.widget.author.get(),job.author)
                    self.assertEqual(self.widget.source_name.get(),Path(job.path).name)
                    self.assertEqual(self.widget.tree.selection(),(job.id,))
                    self.assertEqual(self.widget.tree.focus(),job.id)
                    self.assertEqual(len(self.widget.parsed.chapters),2)
                    self.assertIn(f'{index+1}/3',self.widget.queue_notice.get())
                    gates[index].set()
                self.wait(lambda:self.widget.busy)
                extra_parse.assert_not_called()
        finally:
            for gate in gates:gate.set()
        self.assertEqual(painted,[job.id for job in jobs])
        self.assertEqual(self.widget.current,jobs[-1].id)
        self.assertEqual(len(list((self.folder/'output').glob('*.epub'))),3)
        self.widget.follow_conversion(jobs[0].id,1,3)
        self.assertEqual(self.widget.current,jobs[-1].id)

    def test_stopping_conversion_keeps_current_book_and_ignores_late_follow_events(self):
        self.setup_conversion();jobs=self.widget.jobs;gate=threading.Event();entered=threading.Event()
        def search(title,author,**kwargs):
            entered.set();gate.wait(8);return [self.row(title,author)]
        try:
            with patch('covers.search_covers',search):
                self.widget.start();self.wait(lambda:not entered.is_set() or self.widget.current!=jobs[0].id)
                self.widget.stop();self.widget.follow_conversion(jobs[-1].id,3,3)
                self.assertEqual(self.widget.current,jobs[0].id)
                gate.set();self.wait(lambda:self.widget.busy)
        finally:gate.set()
        self.assertEqual(self.widget.current,jobs[0].id)


if __name__=='__main__':unittest.main(verbosity=2)
