# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
from unittest.mock import patch
import threading
import unittest
import app
import preparation
from batch import Options, run_batch
from covers import SearchResult
import test_bulk_workflows as fixtures


class ProgressFlowTests(unittest.TestCase):
    setUp = fixtures.BulkWorkflowTests.setUp
    tearDown = fixtures.BulkWorkflowTests.tearDown
    wait = fixtures.BulkWorkflowTests.wait
    import_books = fixtures.BulkWorkflowTests.import_books
    row = fixtures.BulkWorkflowTests.row

    def test_24_imports_finish_at_waiting_zero_instead_of_24_of_50(self):
        paths = list(self.sources)
        for index in range(3, 24):
            path = self.folder / f"《测试小说{index}》作者：作者{index}.txt"
            path.write_text("第一章 开始\n正文。\n第二章 尾声\n正文。", encoding="utf-8")
            paths.append(path)
        self.widget.add_paths(paths)
        self.wait(lambda: self.widget.busy or bool(self.widget.preparing))
        self.assertEqual(len(self.widget.jobs), 24)
        self.assertEqual(self.widget.progress.value, 0)
        self.assertEqual(self.widget.progress_phase.get(), "待转换")
        self.assertEqual(self.widget.progress_percent.get(), "0%")

    def test_append_uses_actual_batch_size_and_clears_when_finished(self):
        self.widget.add_paths(self.sources[:1])
        self.wait(lambda: self.widget.busy or bool(self.widget.preparing))
        gate = threading.Event()
        entered = threading.Event()
        original = app.new_job

        def delayed(path):
            if Path(path) == self.sources[2]:
                entered.set()
                gate.wait(4)
            return original(path)

        try:
            with patch.object(app, "new_job", delayed):
                self.widget.add_paths(self.sources[1:])
                self.wait(lambda: not entered.is_set() or self.widget.progress.value != 1)
                self.assertEqual(self.widget.progress.maximum, 2)
                self.assertEqual(self.widget.progress_percent.get(), "50%")
                self.assertEqual(self.widget.progress_phase.get(), "导入书籍")
                gate.set()
                self.wait(lambda: self.widget.busy or bool(self.widget.preparing))
        finally:
            gate.set()
        self.assertEqual(self.widget.progress.value, 0)
        self.assertEqual(self.widget.progress_phase.get(), "待转换")

    def test_one_click_still_scrapes_downloads_and_applies_with_auto_cover_off(self):
        self.import_books()
        self.widget.auto_cover.set(False)
        calls = []
        stages = []
        token = self.widget.progress_phase.trace_add(
            "write", lambda *args: stages.append(self.widget.progress_phase.get())
        )

        def search(title, author, **kwargs):
            calls.append(title)
            kwargs["progress"]("正在下载并检查 1 张候选封面…")
            return SearchResult([self.row(title, author)])

        try:
            with patch.object(preparation, "search_details", search):
                self.widget.prepare_all()
                self.wait(lambda: self.widget.busy)
        finally:
            self.widget.progress_phase.trace_remove("write", token)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(job.cover_path == str(self.cover) for job in self.widget.jobs))
        self.assertIn("封面刮削", stages)
        self.assertIn(
            "联网搜索、下载并应用；已有图片保留，不重新识别目录", self.widget.logs.get("1.0", "end")
        )
        self.assertEqual(self.widget.progress_phase.get(), "待转换")
        self.assertEqual(self.widget.progress_percent.get(), "0%")

    def test_explicit_generated_cover_is_preserved_with_automatic_matching_on(self):
        self.import_books()
        self.widget.clear_cover()
        job = self.widget.current_job()
        self.assertEqual(job.cover_mode, "generated")
        with patch("covers.search_covers") as network:
            report = run_batch(
                [job], Options(str(self.folder / "manual-cover"), ("epub",), auto_cover=True)
            )
        network.assert_not_called()
        self.assertEqual(report["results"][0]["cover_source"], "指定文字封面")
        self.assertEqual(report["results"][0]["cover_type"], "文字封面")


if __name__ == "__main__":
    unittest.main(verbosity=2)
