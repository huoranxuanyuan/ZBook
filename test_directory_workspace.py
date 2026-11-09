# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk
import time
import unittest

from smooth_tree import PixelTreeview
from core import Metadata,parse_text,count_chapters
from batch import Options,run_batch
import test_bulk_workflows as fixtures


class PixelScrollTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.geometry('600x360+30000+30000')
        self.tree=PixelTreeview(self.root,style='Books.Treeview')
        self.tree.pack(fill='both',expand=True)
        for index in range(50):self.tree.insert('','end',iid=str(index),text=f'☑  测试小说 {index}\n     作者 · 2 章')
        self.root.update()
    def tearDown(self):self.root.destroy()
    def settle(self):
        end=time.monotonic()+.4
        while time.monotonic()<end:self.root.update();time.sleep(.01)
    def test_precise_wheel_keeps_partial_rows_and_hit_testing(self):
        self.tree.wheel(SimpleNamespace(delta=-15));self.settle()
        self.assertGreater(self.tree.offset,0)
        self.assertLess(self.tree.offset,self.tree.row_height)
        self.assertLess(self.tree.bbox('0')[1],0)
        y=self.tree.bbox('1')[1]+2
        self.assertEqual(self.tree.identify_row(y),'1')
        self.tree.event_generate('<Button-1>',x=80,y=y);self.root.update()
        self.assertEqual(self.tree.selection(),('1',))
    def test_scrollbar_can_stop_between_rows_and_reach_both_ends(self):
        self.tree.yview_moveto(.23456);self.root.update()
        self.assertGreater(self.tree.offset%self.tree.row_height,.01)
        self.tree.yview_moveto(1);self.root.update()
        self.assertAlmostEqual(self.tree.yview()[1],1)
        self.tree.yview_moveto(0);self.root.update()
        self.assertEqual(self.tree.offset,0)
    def test_large_outline_only_draws_visible_rows_and_keeps_header_fixed(self):
        self.tree.destroy()
        self.tree=PixelTreeview(self.root,columns=('line',),show='tree headings')
        self.tree.pack(fill='both',expand=True);self.tree.heading('#0',text='章节与分卷')
        for index in range(12000):self.tree.insert('','end',iid=str(index),text=f'第{index+1}章',values=(index+1,))
        self.root.update();self.tree.yview_moveto(.500013);self.root.update()
        self.assertLess(len(self.tree.find_all()),150)
        self.assertEqual(self.tree.identify_row(self.tree.header_height-1),'')
        self.assertTrue(self.tree.identify_row(self.tree.header_height+2))


class DirectoryWorkspaceTests(unittest.TestCase):
    setUp=fixtures.BulkWorkflowTests.setUp
    tearDown=fixtures.BulkWorkflowTests.tearDown
    wait=fixtures.BulkWorkflowTests.wait
    import_books=fixtures.BulkWorkflowTests.import_books
    def import_volumes(self):
        path=self.folder/'《分卷测试》作者：青杉.txt'
        path.write_text('书名：分卷测试\n作者：青杉\n简介。\n第一卷 初见\n第一章 新年参拜\n正文甲。\n第二章 约定\n正文乙。\n第二卷 旅途\n第三章 出发\n正文丙。',encoding='utf-8')
        self.widget.add_paths([path]);self.wait(lambda:self.widget.busy or bool(self.widget.preparing))
        scale=self.widget.ui_scale;self.root.geometry(f'{int(1100*scale)}x{int(780*scale)}+30000+30000')
        self.root.deiconify();self.root.update()
        return self.widget.jobs[0]
    def test_volume_defaults_collapsed_then_click_expands_and_previews(self):
        job=self.import_volumes();tree=self.widget.chapter_tree
        volumes=[item for item,node in self.widget.outline_nodes.items() if node['kind']=='volume']
        self.assertEqual(len(volumes),2)
        self.assertTrue(all(not tree.item(item,'open') for item in volumes))
        first=volumes[0];self.assertEqual(tree.selection(),(first,))
        child=tree.get_children(first)[0];self.assertEqual(tree.bbox(child),'')
        x,y,_,height=tree.bbox(first)
        tree.event_generate('<Button-1>',x=80,y=y+height//2);self.root.update()
        self.assertTrue(tree.item(first,'open'));self.assertFalse(tree.item(volumes[1],'open'))
        tree.selection_set(child);self.root.update()
        self.assertIn('第一章',self.widget.preview_title.get())
        self.assertEqual(self.widget.source_name.get(),Path(job.path).name)
        self.assertEqual(self.widget.source_path,job.path)
    def test_actual_chapter_count_excludes_volumes_and_intro_and_batch_agrees(self):
        job=self.import_volumes();self.assertEqual(job.chapter_count,3)
        self.assertIn('3 章',self.widget.book_text(job));self.assertNotIn(' 节',self.widget.book_text(job))
        self.assertIn('3 章 · 2 卷',self.widget.preview_info.get())
        events=[]
        report=run_batch([job],Options(str(self.folder/'output'),('epub',)),emit=lambda *args:events.append(args))
        self.assertEqual(report['results'][0]['chapters'],3)
        self.assertIn(('chapters',job.id,3),events)
    def test_no_volume_remains_direct_chapters_and_clear_resets_filename(self):
        self.import_books();tree=self.widget.chapter_tree
        self.assertEqual(len(tree.get_children('full')),2)
        self.assertTrue(all(node['kind']!='volume' for node in self.widget.outline_nodes.values()))
        self.widget.select_all();self.widget.remove_selected();self.root.update()
        self.assertEqual(self.widget.source_name.get(),'尚未导入文件')
        self.assertEqual(self.widget.source_path,'')
    def test_no_heading_plain_text_is_one_chapter(self):
        book=parse_text('一整篇没有章节标题的正文。',Metadata('测试书','测试作者'))
        self.assertEqual(count_chapters(book),1)


if __name__=='__main__':unittest.main(verbosity=2)
