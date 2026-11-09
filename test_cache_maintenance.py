# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
from unittest.mock import patch
import json
import os
import shutil
import tempfile
import threading
import time
import tkinter as tk
import unittest
import uuid
import hashlib
from dataclasses import asdict

import app
import batch
import cache_maintenance as maintenance
import covers
from core import new_job
from batch import Options,run_batch
import test_bulk_workflows as fixtures


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.root=Path(self.directory.name)
        self.trash=self.root/'retained-test-trash';self.trash.mkdir();self.now=time.time();self.managers=[]
        self.legacy=patch.object(maintenance.CacheMaintenance,'_legacy_active',return_value=False);self.legacy.start()
    def tearDown(self):
        for manager in self.managers:manager.finish()
        self.legacy.stop();self.directory.cleanup()
    def recycle(self,paths):
        for path in paths:shutil.move(str(path),str(self.trash/(uuid.uuid4().hex+'-'+path.name)))
    def manager(self):
        manager=maintenance.CacheMaintenance(self.root,recycle=self.recycle,clock=lambda:self.now);self.managers.append(manager);return manager
    def file(self,path,age=0,content=b'cache'):
        path=self.root/path;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
        os.utime(path,(self.now-age*maintenance.DAY,)*2);return path
    def run_folder(self):return Path('archive/runs/20261108-100000-abcdef')
    def test_successful_publish_recycles_only_intermediates_and_keeps_source_outputs_logs(self):
        source=self.file(Path('source/原书.txt'),content='第一章 开始\n正文。'.encode())
        def factory(root,cover_dir=None,kind='gui'):
            manager=self.manager();manager.kind=kind;return manager
        with patch.object(batch,'ROOT',self.root),patch.object(batch,'create_maintenance',factory):
            report=run_batch([new_job(source)],Options(str(self.root/'output'),('epub',)))
        row=report['results'][0];self.assertEqual(row['status'],'完成');self.assertTrue(row['published_verified'])
        self.assertTrue(Path(row['outputs'][0]).is_file());self.assertTrue(source.is_file())
        self.assertFalse(list((self.root/'archive'/'runs').rglob('book.epub')))
        self.assertTrue(Path(report['report_path']).is_file());self.assertTrue(Path(report['progress_log']).is_file())
        self.assertTrue(list(self.trash.rglob('*.epub')))
    def test_cleanup_failure_does_not_turn_a_successful_conversion_into_failure(self):
        source=self.file(Path('source/原书.txt'),content='第一章 开始\n正文。'.encode())
        def fail(paths):raise PermissionError('文件被占用')
        def factory(root,cover_dir=None,kind='gui'):
            manager=self.manager();manager.recycle=fail;return manager
        with patch.object(batch,'ROOT',self.root),patch.object(batch,'create_maintenance',factory):report=run_batch([new_job(source)],Options(str(self.root/'output'),('epub',)))
        self.assertEqual(report['results'][0]['status'],'完成')
        self.assertTrue(list((self.root/'archive'/'runs').rglob('book.epub')))
        records=''.join(p.read_text(encoding='utf-8') for p in (self.root/'archive'/'maintenance').glob('cleanup-*.jsonl'))
        self.assertIn('retained',records);self.assertIn('文件被占用',records)
    def test_failed_export_keeps_recovery_intermediate_and_error_log(self):
        source=self.file(Path('source/原书.txt'),content='第一章 开始\n正文。'.encode())
        def factory(root,cover_dir=None,kind='gui'):return self.manager()
        with patch.object(batch,'ROOT',self.root),patch.object(batch,'create_maintenance',factory),patch.object(batch,'verify_published_files',side_effect=OSError('校验失败')):
            report=run_batch([new_job(source)],Options(str(self.root/'output'),('epub',)))
        self.assertEqual(report['results'][0]['status'],'部分完成')
        self.assertTrue(list((self.root/'archive'/'runs').rglob('book.epub')))
        self.assertTrue(Path(report['results'][0]['error_log']).exists())
    def test_live_preview_and_conversion_leases_survive_other_instance_cleanup(self):
        first=self.manager();preview=self.root/'archive'/'preview-cache'/('a'*32);first.protect_resource(preview)
        cached=self.file(preview.relative_to(self.root)/('b'*32+'-1.json.gz'),age=10)
        run=self.root/self.run_folder();first.protect_resource(run)
        intermediate=self.file(self.run_folder()/('c'*32)/'book.epub',age=10)
        (run/'report.json').write_text('{"results":[]}',encoding='utf-8')
        second=self.manager();second.sweep()
        self.assertTrue(cached.exists());self.assertTrue(intermediate.exists())
        first.finish();self.assertFalse(cached.exists())
    def test_abnormal_exit_preview_is_cleaned_on_next_start(self):
        first=self.manager();preview=self.root/'archive'/'preview-cache'/('a'*32);first.protect_resource(preview)
        cached=self.file(preview.relative_to(self.root)/('b'*32+'-1.json.gz'))
        maintenance._unlock(first.handle);first.handle.close();first.closed=True
        second=self.manager();second.sweep();self.assertFalse(cached.exists())
    def test_covers_expire_by_use_time_and_budget_but_live_choices_are_kept(self):
        manager=self.manager()
        expired=self.file(Path('data/covers')/('a'*64+'.jpg'),age=8,content=b'x'*60)
        used=self.file(Path('data/covers')/('b'*64+'.jpg'),age=2,content=b'x'*60)
        older=self.file(Path('data/covers')/('c'*64+'.jpg'),age=3,content=b'x'*60)
        newest=self.file(Path('data/covers')/('d'*64+'.jpg'),content=b'x'*60)
        manifest=self.file(Path('data/covers')/('e'*64+'.json'),content=json.dumps([{'path':str(expired)}]).encode())
        manager.protected=[used]
        with patch.object(maintenance,'COVER_LIMIT',125):manager.sweep()
        self.assertFalse(expired.exists());self.assertFalse(older.exists());self.assertFalse(manifest.exists())
        self.assertTrue(used.exists());self.assertTrue(newest.exists())
    def test_cache_hit_refreshes_image_last_use(self):
        manager=self.manager();image=self.file(Path('data/covers')/('a'*64+'.jpg'),age=8)
        class Row:path=str(image)
        maintenance.touch_covers([Row()],image.parent);manager.sweep();self.assertTrue(image.exists())
    def test_shared_covers_not_cleaned_while_another_instance_uses_root(self):
        first=self.manager();image=self.file(Path('data/covers')/('a'*64+'.jpg'),age=8)
        second=self.manager();second.sweep();self.assertTrue(image.exists())
        first.finish();second.sweep();self.assertFalse(image.exists())
    def test_30_day_logs_expire_but_current_logs_settings_and_outputs_are_untouched(self):
        manager=self.manager()
        old=self.file(Path('archive/logs/application-20260101.log'),age=31)
        new=self.file(Path('archive/logs/application-20261108.log'))
        settings=self.file(Path('data/session.json'),age=90)
        output=self.file(Path('output/book.epub'),age=90)
        unknown=self.file(Path('archive/logs/用户笔记.txt'),age=90)
        manager.sweep();self.assertFalse(old.exists())
        for path in (new,settings,output,unknown):self.assertTrue(path.exists())
    def test_outputs_inside_work_directory_are_never_recycled(self):
        manager=self.manager();work=self.root/self.run_folder()/('a'*32)
        output=self.file(work.relative_to(self.root)/'book.epub')
        cache=self.file(work.relative_to(self.root)/'book.mobi')
        manager.cleanup_work(work,[output]);self.assertTrue(output.exists());self.assertFalse(cache.exists())
        manager.sweep();self.assertTrue(output.exists())
    def test_outside_paths_and_unrecognized_cache_files_are_not_recycled(self):
        manager=self.manager();source=self.file(Path('source/book.epub'))
        self.assertEqual(manager._recycle([source],'test'),0);self.assertTrue(source.exists())
        unknown=self.file(Path('archive/preview-cache')/('a'*32)/'原文.txt',age=10)
        manager.sweep();self.assertTrue(unknown.exists())
    def test_legacy_cache_gets_a_grace_period_and_recent_logs_remain(self):
        manager=self.manager();folder=Path('archive/preview-cache')/('a'*32)
        cached=self.file(folder/('b'*32+'-1.json.gz'))
        with patch.object(manager,'_legacy_active',return_value=True):manager.sweep()
        self.assertTrue(cached.exists())
        os.utime(cached.parent,(self.now-2*maintenance.DAY,)*2)
        manager.sweep();self.assertFalse(cached.exists())
    def test_cover_usage_does_not_extend_original_search_freshness(self):
        image=self.file(Path('data/covers')/('a'*64+'.jpg'),age=6)
        row=covers.Cover(str(image),'测试作品','https://example.org','https://example.org/image.jpg',120,160,'测试来源','测试作者','书名、作者匹配')
        key=hashlib.sha256(('v4-sources\0测试作品\0测试作者').encode('utf-8')).hexdigest()
        manifest=self.file(Path('data/covers')/(key+'.json'),age=.02,content=json.dumps([asdict(row)],ensure_ascii=False).encode('utf-8'))
        stamp=manifest.stat().st_mtime
        with patch.object(covers,'CACHE',image.parent),patch.object(covers,'create_maintenance',side_effect=lambda *args,**kwargs:self.manager()),patch.object(covers,'PROVIDERS',{}):
            result=covers.search_details('测试作品','测试作者')
        self.assertEqual(len(result.covers),1);self.assertEqual(manifest.stat().st_mtime,stamp)
        self.assertGreater(image.stat().st_mtime,self.now-maintenance.DAY)


class SafeExitTests(unittest.TestCase):
    setUp=fixtures.BulkWorkflowTests.setUp
    tearDown=fixtures.BulkWorkflowTests.tearDown
    wait=fixtures.BulkWorkflowTests.wait
    import_books=fixtures.BulkWorkflowTests.import_books
    def test_exit_waits_for_pending_snapshot_write_without_deleting_other_window_cache(self):
        self.import_books();main_cache=self.widget.preview_store.root
        second_root=tk.Toplevel(self.root);second_root.withdraw();second=app.ZBook(second_root)
        second.add_paths(self.sources[:1]);self.wait(lambda:second.busy or bool(second.preparing))
        gate=threading.Event();entered=threading.Event();save=second.preview_store.save
        def delayed(*args):entered.set();gate.wait(5);return save(*args)
        try:
            with patch.object(second.preview_store,'save',delayed):
                second.prepare_job(second.jobs[0]);self.assertTrue(entered.wait(2));second.close()
                self.assertTrue(second_root.winfo_exists());self.assertFalse(second.shutdown_ready.is_set())
                self.assertTrue(main_cache.exists());gate.set()
                self.wait(lambda:not second.shutdown_ready.is_set());self.wait(lambda:bool(second_root.winfo_exists()))
            self.assertFalse(second.preview_store.root.exists());self.assertTrue(main_cache.exists())
        finally:
            gate.set()
            if second_root.winfo_exists():second.analysis_shutdown.set();second.analysis_pool.shutdown(wait=True,cancel_futures=True);second.maintenance.finish();second_root.destroy()


if __name__=='__main__':unittest.main(verbosity=2)
