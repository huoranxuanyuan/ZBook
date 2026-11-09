# SPDX-License-Identifier: AGPL-3.0-only
"""Standalone desktop application. UI calls core/export services directly."""
from __future__ import annotations
from dataclasses import asdict
from io import BytesIO
from pathlib import Path
from safe_image import open_image
import copy
import ctypes
import hashlib
import json
import logging
import os
import queue
import threading
import time
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from PIL import Image, ImageTk, ImageGrab, ImageDraw
from core import BookJob, new_job, read_job, count_chapters
from input_sources import SUPPORTED_SUFFIXES
from batch_limits import MAX_BATCH_FILES, LIMIT_DESCRIPTION, collect_sources, import_summary, validate_batch
from preparation import prepare_covers
from batch import Options, run_batch, find_converter
from covers import quality_label, search_covers, search_details
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from ui_widgets import SoftButton, NativeFileDrop, SlimScrollbar
from reading_style import PROFILE, LINE_HEIGHT, PARAGRAPH_GAP, migrate_settings
from branding import VERSION, APP_NAME, application_icon
from paths import ROOT, DATA
from epub_export import generated_cover, cover_bytes
from cover_design import normalize_cover_style
from layout_widgets import SmoothScrollCanvas
from preview_cache import SessionPreviewCache
from cache_maintenance import create_maintenance


RULES=('自动（中文 / 英文）','数字标题','自定义正则')
ENCODINGS=('自动','utf-8','gb18030','big5','utf-16','utf-16le','utf-16be')

class ZBook:
    def __init__(self, root):
        self.root=root;self.jobs=[];self.current=None;self.parsed=None;self.preview_token=0
        self.events=queue.Queue();self.busy=False;self.cancel=threading.Event();self.closing=False
        self.operation=None;self.import_token=None;self.importing=False;self.worker=None;self.prepare_report=None;self.imported_paths=set()
        self.analysis_slots=threading.BoundedSemaphore(2);self.analysis_shutdown=threading.Event()
        self.preparing=set();self.analysis_versions={};self.import_follow_versions={};self.book_cache=OrderedDict();self.catalogs={};self.analysis_pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='book-analysis');self.cover_windows={}
        self.outline_nodes={};self.outline_messages={};self.progress_log=''
        self.preview_store=SessionPreviewCache(ROOT/'archive'/'preview-cache');self.preview_future=None
        self.maintenance=create_maintenance(ROOT,DATA/'covers');self.maintenance.protect_resource(self.preview_store.root)
        self.shutdown_started=False;self.shutdown_ready=threading.Event();self.maintenance_after=None
        self.queue_thumbnails={};self.cover_results={};self.directory_errors={};self.outline_run={}
        self.analysis_futures={};self.preview_lines={};self.images=[];self.report_path='';self.loading=False;self.controls=[]
        DATA.mkdir(parents=True,exist_ok=True)
        self.settings={'output':str(ROOT/'output'),'converter':'','epub':False,'mobi':True,'auto_cover':True,'line_height':f'{LINE_HEIGHT:g}','paragraph_gap':f'{PARAGRAPH_GAP:g}'}
        self.restore()
        self.settings['cover_style']=normalize_cover_style(self.settings.get('cover_style'))
        if self.settings.get('cover_pipeline_profile')!='automatic-cover-1':
            self.settings.setdefault('auto_cover',True)
            self.settings['cover_pipeline_profile']='automatic-cover-1'
        if self.settings.get('output_profile')!='mobi-default-1':self.settings.update(epub=False,mobi=True,output_profile='mobi-default-1')
        migrate_settings(self.settings)
        self.build()
        self.refresh_queue()
        self.root.protocol('WM_DELETE_WINDOW',self.close)
        self.root.after(100,self.pump)
        self.persist();self.schedule_maintenance()

    def restore(self):
        # Preferences survive; queues, selected covers and conversion history do not.
        p=DATA/'session.json'
        if p.exists():
            try:self.settings.update(json.loads(p.read_text(encoding='utf8')).get('settings',{}))
            except Exception:logging.exception('读取偏好设置失败')
        self.jobs=[]

    def persist(self):
        retry=getattr(self,'preferences_retry',None)
        if retry is not None:
            self.root.after_cancel(retry);self.preferences_retry=None
        self.settings.update({'output':self.output.get(),'converter':self.converter.get(),'epub':self.epub.get(),'mobi':self.mobi.get(),'auto_cover':self.auto_cover.get(),'line_height':self.line_height.get(),'paragraph_gap':self.gap.get()})
        payload=json.dumps({'settings':self.settings,'jobs':[]},ensure_ascii=False,indent=2)
        if getattr(self,'saved_preferences',None)==payload:return
        p=DATA/'session.json';tmp=DATA/f'session-{os.getpid()}.pending'
        try:
            tmp.write_text(payload,encoding='utf8');tmp.replace(p)
            self.saved_preferences=payload;self.preferences_failures=0
        except OSError:
            self.preferences_failures=getattr(self,'preferences_failures',0)+1
            if self.preferences_failures<=3 and not self.closing:
                self.preferences_retry=self.root.after(150,self.persist)
            else:logging.exception('偏好设置暂未写入，已保留原设置')

    def build(self):
        from ui_layout import build_ui
        build_ui(self)

    def schedule_preview_resize(self,event=None):
        if self.preview_resize_job is not None:
            self.root.after_cancel(self.preview_resize_job)
        self.preview_resize_job=self.root.after_idle(self.resize_preview)

    def resize_preview(self):
        self.preview_resize_job=None
        width=self.preview_split.winfo_width()
        if width<10:return
        panes=self.preview_split.panes();left_min=int(self.preview_split.panecget(panes[0],'minsize'));right_min=int(self.preview_split.panecget(panes[1],'minsize'))
        sash=int(self.preview_split.cget('sashwidth'))
        position=max(left_min,min(int(width*self.preview_ratio),width-right_min-sash))
        self.preview_split.sash_place(0,position,0)

    def remember_preview_ratio(self,event=None):
        width=self.preview_split.winfo_width()
        if width>10:self.preview_ratio=self.preview_split.sash_coord(0)[0]/width

    def button(self,parent,text,command,pack=None,managed=True,primary=False):
        btn=SoftButton(parent,text=text,command=command,primary=primary,height=34)
        btn.pack(**(pack or {'side':'left','padx':(0,6)}))
        if managed:self.controls.append(btn)
        return btn

    def field(self,parent,label,var):
        row=ttk.Frame(parent);row.pack(fill='x',pady=4)
        ttk.Label(row,text=label,width=9).pack(side='left')
        entry=ttk.Entry(row,textvariable=var);entry.pack(side='left',fill='x',expand=True);self.controls.append(entry)

    def current_job(self):
        return next((j for j in self.jobs if j.id==self.current),None)

    def refresh_queue(self):
        current=self.current;position=self.tree.yview()[0];focus=self.tree.focus()
        for item in self.tree.get_children():self.tree.delete(item)
        for job in self.jobs:
            self.tree.insert('','end',iid=job.id,text=self.book_text(job),values=self.queue_values(job),image=self.queue_thumbnail(job))
        if current and self.tree.exists(current):self.tree.selection_set(current)
        if focus and self.tree.exists(focus):self.tree.focus(focus)
        self.tree.yview_moveto(position)
        self.summary.set(f'{len(self.jobs)} 本 · 已选 {sum(j.selected for j in self.jobs)}')
        if hasattr(self,'start_button'):self.update_start()

    def refresh_job(self,job):
        if self.tree.exists(job.id):self.tree.item(job.id,text=self.book_text(job),values=self.queue_values(job),image=self.queue_thumbnail(job))
        self.update_start()

    def on_queue_click(self,event):
        if self.busy:return
        item=self.tree.identify_row(event.y)
        if item and event.x<int(28*self.ui_scale):
            job=next(j for j in self.jobs if j.id==item);job.selected=not job.selected;self.refresh_queue();self.persist()

    def add_files(self):
        paths=filedialog.askopenfilenames(title='导入小说或文档',filetypes=[('小说和文档','*.txt *.docx *.doc *.rtf *.pdf'),('TXT 文本','*.txt'),('Word 文档','*.docx *.doc *.rtf'),('PDF 文档','*.pdf'),('所有文件','*.*')])
        self.add_paths(paths)

    def add_folder(self):
        folder=filedialog.askdirectory(title='选择书籍文件夹（包含子文件夹）')
        if folder:self.add_paths([folder])

    def show_progress(self,phase,done=0,total=1):
        total=max(1,float(total));done=max(0,min(total,float(done)))
        self.progress.configure(value=done,maximum=total)
        self.progress_phase.set(phase);self.progress_percent.set(f'{done/total:.0%}')

    def add_paths(self,paths):
        values=list(paths)
        if not values or self.busy:return
        if len(values)>MAX_BATCH_FILES:self.reject_import();return
        if len(self.jobs)>=MAX_BATCH_FILES:
            self.reject_import();return
        known=[job.path for job in self.jobs]
        self.import_token=object();token=self.import_token;self.importing=True;self.operation='import'
        self.busy=True;self.cancel.clear();self.lock_controls(True)
        self.status.set('正在后台检查导入文件，最多 50 本…');self.queue_notice.set('正在后台导入，可随时停止');self.queue_notice_label.configure(fg='#7A8595')
        self.show_progress('导入书籍')
        def work():
            batch=None;errors=[];count=0
            try:
                batch=collect_sources(values,known,self.cancel)
                for index,path in enumerate(batch.paths,1):
                    if self.cancel.is_set():break
                    try:
                        batch.attempted.append(path)
                        job=new_job(path);job.status='正在整理目录'
                        self.events.put(('import_job',token,job));count+=1
                    except Exception as exc:errors.append(path.name+': '+str(exc))
                    self.events.put(('import_progress',token,index,len(batch.paths)))
                batch.stopped=self.cancel.is_set()
            except Exception as exc:errors.append(str(exc))
            self.events.put(('import_done',token,batch,count,errors))
        self.worker=threading.Thread(target=work,daemon=True);self.worker.start()

    def toggle_all(self):
        if self.busy:return
        value=not all(job.selected for job in self.jobs)
        for job in self.jobs:job.selected=value
        self.refresh_queue();self.persist()

    def remove_current(self):
        if self.current:self.remove_jobs({self.current})

    def remove_selected(self):
        self.remove_jobs({job.id for job in self.jobs if job.selected})

    def remove_jobs(self,identifiers):
        if self.busy:return
        targets={job.id for job in self.jobs if job.id in identifiers}
        if not targets:self.status.set('请先勾选要移出的书籍');return
        previous_index=next((i for i,job in enumerate(self.jobs) if job.id==self.current),0)
        removing_current=self.current in targets
        if not removing_current:self.save_fields()
        for job_id in targets:
            window=self.cover_windows.pop(job_id,None)
            if window and window.winfo_exists():window.destroy()
            self.preview_lines.pop(job_id,None);self.queue_thumbnails.pop(job_id,None);self.cover_results.pop(job_id,None);self.directory_errors.pop(job_id,None);self.invalidate_analysis(job_id)
            self.analysis_versions.pop(job_id,None)
        self.jobs=[job for job in self.jobs if job.id not in targets]
        if removing_current:self.current=None;self.reset_details()
        self.refresh_queue()
        if self.jobs and not self.current:self.tree.selection_set(self.jobs[min(previous_index,len(self.jobs)-1)].id)
        self.status.set(f'已移出 {len(targets)} 本，原文件保留');self.persist()

    def clear_queue(self):
        if self.busy:return
        if self.jobs and messagebox.askyesno('清空队列','移出所有书籍？原文件和已导出的电子书都会保留。'):
            self.remove_jobs({job.id for job in self.jobs})

    def reset_details(self):
        self.title.set('');self.author.set('');self.source_name.set('尚未导入文件');self.source_path='';self.cover_info.set('');self.notes.set('选中一本书，查看和编辑信息。')
        self.chapter_tree.delete(*self.chapter_tree.get_children());self.parsed=None
        self.set_text(self.body,'');self.preview_title.set('正文预览');self.preview_info.set('尚未选择书籍');self.placeholder_cover()

    def on_select(self,event=None,load_preview=True):
        selection=self.tree.selection()
        if not selection or selection[0]==self.current:return
        if self.current and not self.busy:self.save_fields()
        self.current=selection[0];job=self.current_job()
        if not job:return
        self.title.set(job.title);self.author.set(job.author);self.encoding.set(job.encoding);self.rule.set(job.rule);self.regex.set(job.custom_regex)
        self.source_name.set(Path(job.path).name);self.source_path=job.path
        self.notes.set('\n'.join(job.notes) or '可直接修改书名、作者，再保存。')
        self.preview_cover()
        if load_preview:self.load_preview()

    def save_fields(self):
        job=self.current_job()
        if not job:return False
        title=self.title.get().strip()
        if not title:return False
        parse_changed=(job.encoding,job.rule,job.custom_regex)!=(self.encoding.get(),self.rule.get(),self.regex.get())
        changed=(job.title,job.author)!=(title,self.author.get().strip())
        job.title=title;job.author=self.author.get().strip();job.encoding=self.encoding.get();job.rule=self.rule.get();job.custom_regex=self.regex.get()
        if job.author:job.notes=[n for n in job.notes if '未识别作者' not in n]
        if parse_changed:
            self.invalidate_analysis(job.id);self.prepare_job(job)
        elif changed:
            job.status='待转换'
            if job.id in self.book_cache:
                self.book_cache[job.id].metadata.title=job.title;self.book_cache[job.id].metadata.author=job.author
        self.refresh_job(job)
        return True

    def apply_current(self):
        if not self.current_job():return
        if not self.save_fields():messagebox.showwarning('书名为空','请填写书名');return
        self.persist();self.preview_cover();self.load_preview()

    def load_preview(self):
        job=self.current_job()
        if not job:return
        self.preview_token+=1;self.parsed=None;self.outline_nodes={};self.chapter_tree.delete(*self.chapter_tree.get_children())
        self.preview_title.set('正文预览')
        if self.preview_future is not None:self.preview_future.cancel()
        if job.id in self.book_cache:
            self.book_cache.move_to_end(job.id);self.display_book(self.book_cache[job.id]);return
        if self.operation=='convert' and job.id==getattr(self,'processing_job_id',None):
            self.preview_info.set('正在读取当前书籍的目录…');self.set_text(self.body,'转换正在读取当前书籍。');return
        if job.id in self.preparing:
            self.preview_info.set('正在整理目录…');self.set_text(self.body,'导入时正在自动识别目录，请稍候。');return
        version=self.analysis_versions.get(job.id,0)
        if self.preview_store.exists(job.id,version):
            token=self.preview_token;job_id=job.id
            self.preview_info.set('正在载入已整理的目录…');self.set_text(self.body,'正在载入本次导入的预览缓存。')
            def work():
                try:self.events.put(('cached_preview',job_id,token,version,self.preview_store.load(job_id,version)))
                except Exception as exc:self.events.put(('cached_preview_error',job_id,token,version,str(exc)))
            self.preview_future=self.analysis_pool.submit(work)
            return
        failed=self.directory_errors.get(job.id)
        self.preview_info.set('目录识别失败' if failed else '目录尚未就绪')
        self.set_text(self.body,(failed+'\n\n' if failed else '')+'点击“重新识别目录”可以重新处理。点击书籍列表只切换预览，不会重新识别。')

    def set_text(self,widget,text):
        widget.configure(state='normal');widget.delete('1.0','end');widget.insert('1.0',text);widget.configure(state='disabled')

    def show_chapter(self,event=None):
        from outline import show_selection
        show_selection(self)

    def rename_chapter(self):
        from outline import rename_selection
        rename_selection(self)


    def reset_chapters(self):
        job=self.current_job()
        if job:
            job.chapter_titles={};job.status='待转换'
            self.prepare_job(job)

    def placeholder_cover(self):
        im=open_image(BytesIO(generated_cover('','',True,style=self.settings.get('cover_style','modern'))));im.thumbnail(self.cover_preview_size);self.cover_photo=ImageTk.PhotoImage(im,master=self.root);self.cover_label.configure(image=self.cover_photo)

    def preview_cover(self):
        job=self.current_job()
        if not job:self.placeholder_cover();return
        try:
            raw=cover_bytes(job.cover_path,job.title,job.author,self.settings.get('cover_style','modern'));im=open_image(BytesIO(raw));im.thumbnail(self.cover_preview_size)
            self.cover_photo=ImageTk.PhotoImage(im,master=self.root);self.cover_label.configure(image=self.cover_photo)
            self.cover_info.set(job.cover_source if job.cover_path else '文字封面 · 已手动指定' if job.cover_mode=='generated' else '尚无图片封面 · 点击“一键搜索封面”或转换时自动匹配')
        except Exception as exc:
            self.placeholder_cover();self.cover_info.set('封面无法读取：'+str(exc))

    def assign_local_image(self,image,source):
        job=self.current_job()
        if not job:return
        out=BytesIO();image.convert('RGB').save(out,'JPEG',quality=95);raw=out.getvalue()
        folder=DATA/'covers';folder.mkdir(exist_ok=True);path=folder/(hashlib.sha256(raw).hexdigest()+'.jpg')
        if not path.exists():path.write_bytes(raw)
        else:os.utime(path,None)
        job.cover_path=str(path);job.cover_source=source;job.cover_mode='image';job.status='待转换';self.cover_results.pop(job.id,None);self.preview_cover();self.refresh_job(job);self.persist()

    def import_cover(self):
        if not self.current_job():messagebox.showinfo('未选书籍','请先选择一本书');return
        path=filedialog.askopenfilename(title='选择封面图片',filetypes=[('图片','*.jpg *.jpeg *.png *.webp *.bmp'),('所有文件','*.*')])
        if path:
            try:
                raw=cover_bytes(path,'','');self.assign_local_image(open_image(BytesIO(raw)),'本地导入：'+Path(path).name)
            except Exception as exc:messagebox.showerror('封面导入失败',str(exc))

    def paste_cover(self):
        if not self.current_job():return
        try:
            value=ImageGrab.grabclipboard()
            if isinstance(value,Image.Image):self.assign_local_image(value,'剪贴板图片')
            elif isinstance(value,list) and value:
                raw=cover_bytes(value[0],'','');self.assign_local_image(open_image(BytesIO(raw)),'剪贴板文件')
            else:messagebox.showinfo('没有图片','请先复制一张图片，再点击粘贴封面')
        except Exception as exc:messagebox.showerror('粘贴失败',str(exc))

    def clear_cover(self):
        job=self.current_job()
        if job:job.cover_path='';job.cover_source='';job.cover_mode='generated';job.status='待转换';self.cover_results.pop(job.id,None);self.preview_cover();self.refresh_job(job);self.persist()

    def online_cover(self):
        if not self.save_fields():messagebox.showinfo('选择书籍','请先选择一本书并填写书名');return
        job=self.current_job();existing=self.cover_windows.get(job.id)
        if existing and existing.winfo_exists():existing.lift();return
        win=tk.Toplevel(self.root);win.title('搜索封面 · '+job.title);win.geometry(f'{int(880*self.ui_scale)}x{int(720*self.ui_scale)}');win.minsize(int(740*self.ui_scale),int(550*self.ui_scale));win.configure(bg='#ffffff');win.transient(self.root)
        self.cover_windows[job.id]=win;win.photos=[];win.diagnostics=[];win.query=tk.StringVar(value=job.title);win.message=tk.StringVar(value='正在查询书籍站点与搜图来源…')
        header=ttk.Frame(win,padding=18);header.pack(fill='x');ttk.Label(header,text='选择封面',font=('Microsoft YaHei UI',16,'bold')).pack(anchor='w')
        row=ttk.Frame(header);row.pack(fill='x',pady=(12,0));ttk.Entry(row,textvariable=win.query).pack(side='left',fill='x',expand=True)
        self.button(row,'扩大搜索',lambda:self.request_covers(job.id,win.query.get(),True,True),pack={'side':'right','padx':(8,0)},managed=False)
        self.button(row,'重新搜索',lambda:self.request_covers(job.id,win.query.get(),True),pack={'side':'right','padx':(10,0)},managed=False,primary=True)
        ttk.Label(header,text='优先匹配书名与作者，图片候选请核对后选用。',foreground='#94949f').pack(anchor='w',pady=(10,0))
        ttk.Label(win,textvariable=win.message,padding=(18,4),foreground='#007aff',wraplength=int(820*self.ui_scale)).pack(fill='x')
        win.progress=ttk.Progressbar(win,mode='indeterminate');win.progress.pack(fill='x',padx=18,pady=8);win.progress.start(15)
        frame=ttk.Frame(win);frame.pack(fill='both',expand=True,padx=12)
        frame.columnconfigure(0,weight=1);frame.rowconfigure(0,weight=1)
        win.canvas=SmoothScrollCanvas(frame,bg='#ffffff',highlightthickness=0,width=1,height=1);scroll=SlimScrollbar(frame,command=win.canvas.yview)
        win.canvas.configure(yscrollcommand=scroll.set);win.canvas.grid(row=0,column=0,sticky='nsew');scroll.grid(row=0,column=1,sticky='ns')
        win.tiles=ttk.Frame(win.canvas);win.tile_window=win.canvas.create_window((0,0),window=win.tiles,anchor='nw');win.tile_widgets=[];win.search_version=None
        win.tiles.bind('<Configure>',lambda e:win.canvas.configure(scrollregion=win.canvas.bbox('all')))
        win.canvas.bind('<Configure>',lambda e:self.layout_covers(win,e.width))
        win.bind('<MouseWheel>',win.canvas.wheel)
        footer=ttk.Frame(win,padding=16);footer.pack(fill='x')
        def browser(engine):
            import webbrowser,urllib.parse
            query=win.query.get().strip()
            urls={'Google':'https://www.google.com/search?'+urllib.parse.urlencode({'q':query,'udm':2}),
                  '百度':'https://image.baidu.com/search/index?'+urllib.parse.urlencode({'tn':'baiduimage','word':query})}
            webbrowser.open(urls[engine])
        self.button(footer,'Google 图片',lambda:browser('Google'),managed=False)
        self.button(footer,'百度图片',lambda:browser('百度'),managed=False)
        self.button(footer,'检索详情',lambda:messagebox.showinfo('检索详情','\n'.join(win.diagnostics) or '正在检索…',parent=win),managed=False)
        self.request_covers(job.id,job.title,False)

    def gallery(self,job_id,rows,matched_count=0):
        job=next((j for j in self.jobs if j.id==job_id),None);win=self.cover_windows.get(job_id)
        if not job or not win or not win.winfo_exists():return
        win.progress.stop();win.message.set(f'找到 {len(rows)} 张封面候选' if rows else '已找到候选，但图片暂未能下载。可稍后重试，或导入本地图片。' if matched_count else '暂无准确匹配。可修改关键词重试，或使用本地图片。')
        def choose(row):
            if self.busy:return
            job.cover_path=row.path;job.cover_source=f'{row.provider} · {row.author or "作者待核对"}';job.cover_mode='image';job.status='待转换'
            self.cover_results.pop(job.id,None);self.refresh_job(job)
            if job.id==self.current:self.preview_cover()
            self.persist();win.destroy()
        for child in win.tiles.winfo_children():child.destroy()
        win.photos=[];win.tile_widgets=[];win.cached_paths=[row.path for row in rows]
        for i,row in enumerate(rows):
            tile=ttk.Frame(win.tiles,padding=12);win.tile_widgets.append(tile)
            im=open_image(row.path);im.thumbnail((int(190*self.ui_scale),int(250*self.ui_scale)));photo=ImageTk.PhotoImage(im);win.photos.append(photo)
            ttk.Label(tile,image=photo,anchor='center').pack()
            ttk.Label(tile,text=row.label,wraplength=int(230*self.ui_scale),justify='left',font=('Microsoft YaHei UI',10,'bold')).pack(fill='x',pady=(10,3))
            ttk.Label(tile,text=row.provider+(' · '+row.author if row.author else ''),wraplength=int(230*self.ui_scale),foreground='#6e6e73',font=('Microsoft YaHei UI',9)).pack(fill='x')
            ttk.Label(tile,text=f'{row.width} × {row.height} · {quality_label(row.width,row.height)}',foreground='#007aff',font=('Microsoft YaHei UI',9)).pack(fill='x',pady=(3,0))
            ttk.Label(tile,text=row.match,wraplength=int(230*self.ui_scale),foreground='#007aff' if '书名、作者匹配'==row.match else '#73737a',font=('Microsoft YaHei UI',9)).pack(fill='x',pady=3)
            self.button(tile,'使用这张封面',lambda r=row:choose(r),pack={'fill':'x','pady':(6,0)},managed=False,primary=True)
        self.layout_covers(win)

    def layout_covers(self,win,width=None):
        width=width or win.canvas.winfo_width()
        if width<2:return
        win.canvas.itemconfigure(win.tile_window,width=width)
        columns=max(1,min(len(win.tile_widgets) or 1,3,int(width/(260*self.ui_scale))))
        for col in range(3):win.tiles.columnconfigure(col,weight=1 if col<columns else 0,minsize=0)
        for i,tile in enumerate(win.tile_widgets):tile.grid(row=i//columns,column=i%columns,padx=6,pady=6,sticky='n')

    def open_output(self):
        try:
            value=self.output.get().strip()
            if not value:raise ValueError('请先选择输出文件夹')
            target=Path(value).expanduser();target.mkdir(parents=True,exist_ok=True)
            os.startfile(str(target.resolve()))
        except (OSError,ValueError) as exc:messagebox.showerror('无法打开输出文件夹',str(exc),parent=self.root)

    def choose_output(self):
        path=filedialog.askdirectory(title='选择电子书输出目录',initialdir=self.output.get())
        if path:self.output.set(path);self.persist()

    def choose_converter(self):
        path=filedialog.askopenfilename(title='选择 MOBI 编码器',filetypes=[('电子书编码器','*.exe')])
        if path:self.converter.set(path);self.persist()

    def open_report(self):
        target=Path(self.progress_log) if self.progress_log else None
        if target and target.is_file():os.startfile(str(target))
        elif self.report_path and Path(self.report_path).parent.is_dir():os.startfile(str(Path(self.report_path).parent))
        else:messagebox.showinfo('暂无转换日志','开始转换后，可在这里打开进度日志；错误详情也会保存。',parent=self.root)

    def start(self):
        if self.busy:return
        if any(j.selected and j.id in self.preparing for j in self.jobs):
            messagebox.showinfo('正在整理目录','导入的目录正在自动整理，完成后即可转换');return
        if self.current and not self.save_fields():messagebox.showwarning('书名为空','请先填写书名');return
        formats=tuple(f for f,var in [('epub',self.epub),('mobi',self.mobi)] if var.get())
        try:
            if not formats:raise ValueError('请至少选择一种输出格式')
            if not any(j.selected for j in self.jobs):raise ValueError('请勾选要转换的书籍')
            if not self.output.get().strip():raise ValueError('请选择输出目录')
            if 'mobi' in formats and not find_converter(self.converter.get()):raise ValueError('没有找到 MOBI 编码器，请在“编码器设置”选择 Calibre 的 ebook-convert.exe，或仅输出 EPUB')
            opts=Options(self.output.get(),formats,self.converter.get(),self.auto_cover.get(),float(self.line_height.get()),float(self.gap.get()),self.settings.get('cover_style','modern'))
            if not .9<=opts.line_height<=3 or not 0<=opts.paragraph_gap<=3:raise ValueError('行距须在 0.9–3，段距须在 0–3')
            validate_batch([job for job in self.jobs if job.selected])
            snapshot=copy.deepcopy(self.jobs)
            self.persist();self.busy=True;self.operation='convert';self.cancel.clear();self.lock_controls(True)
            self.set_text(self.logs,'');self.workspace_tabs.select(1)
            self.show_progress('转换进度',0,sum(j.selected for j in snapshot));self.status.set('开始转换…')
            def work():
                try:self.events.put(('done',run_batch(snapshot,opts,lambda *e:self.events.put(e),self.cancel)))
                except Exception as exc:
                    folder=ROOT/'archive'/'logs';folder.mkdir(parents=True,exist_ok=True)
                    error_path=folder/('conversion-error-'+str(time.time_ns())+'.log');error_path.write_text(traceback.format_exc(),encoding='utf-8')
                    self.events.put(('batch_error',str(exc),str(error_path)))
            threading.Thread(target=work,daemon=True).start()
        except Exception as exc:messagebox.showerror('无法开始转换',str(exc))

    def stop(self):
        if self.busy:
            self.cancel.set();self.status.set('停止请求已收到，当前步骤结束后停止…');self.stop_button.state(['disabled'])
            if self.operation in ('import','outline'):
                for job in self.jobs:
                    if job.id in self.preparing:self.invalidate_analysis(job.id);job.status='待整理';self.refresh_job(job)
            self.complete_preparation();self.complete_outline()

    def lock_controls(self,locked):
        for control in self.controls:
            if locked:control.state(['disabled'])
            else:control.state(['!disabled'])
        self.start_button.configure(state='disabled' if locked else 'normal')
        self.stop_button.configure(state='normal' if locked else 'disabled')
        self.update_start()

    def log(self,text):
        line=time.strftime('%H:%M:%S')+'  '+text+'\n'
        self.logs.configure(state='normal');self.logs.insert('end',line)
        if int(self.logs.index('end-1c').split('.')[0])>600:self.logs.delete('1.0','100.0')
        self.logs.see('end');self.logs.configure(state='disabled')
        if self.operation in ('prepare','outline') and self.progress_log:
            with Path(self.progress_log).open('a',encoding='utf-8') as stream:stream.write(line)

    def pump(self):
        try:
            deadline=time.monotonic()+.025
            while time.monotonic()<deadline:
                event=self.events.get_nowait();kind=event[0]
                if kind=='import_job':
                    self.receive_import_job(event[1],event[2])
                elif kind=='import_progress':
                    if self.operation=='import' and self.import_token is event[1]:
                        self.show_progress('导入书籍',event[2],event[3]);self.status.set(f'正在导入 {event[2]}/{event[3]} 本…')
                elif kind=='import_done':
                    self.finish_import(event)
                elif kind=='batch_current':
                    self.follow_conversion(event[1],event[2],event[3])
                    break
                elif kind=='batch_book':
                    self.receive_conversion_book(event[1],event[2])
                elif kind=='prepare_current':
                    self.follow_preparation(event[1],event[2],event[3])
                    break  # Render this book before processing its queued results.
                elif kind=='prepare_progress':
                    if self.operation=='prepare' and not self.cancel.is_set():
                        self.show_progress('封面刮削',event[1],event[2]);self.status.set(event[3])
                elif kind=='prepared_cover':
                    self.receive_prepared_cover(event[1])
                    break  # Let the cover paint before moving to the next book.
                elif kind=='prepare_done':
                    self.prepare_report=event[1];self.complete_preparation()
                elif kind in ('cached_preview','cached_preview_error'):
                    self.receive_cached_preview(event)
                elif kind in ('analysis_ready','analysis_error'):
                    if self.finish_analysis(event):break
                elif kind=='cover_progress':
                    win=self.cover_windows.get(event[1])
                    if win and win.winfo_exists() and win.search_version==event[2]:win.message.set(event[3]);win.diagnostics.append(event[3])
                elif kind=='cover_result':
                    win=self.cover_windows.get(event[1])
                    if win and win.winfo_exists() and win.search_version==event[2]:win.diagnostics=event[3].diagnostics;self.gallery(event[1],event[3].covers,getattr(event[3],'matched_count',0))
                elif kind=='cover_failure':
                    win=self.cover_windows.get(event[1])
                    if win and win.winfo_exists() and win.search_version==event[2]:win.progress.stop();win.message.set(event[3]);win.diagnostics.append(event[3])
                elif kind=='cover_applied':
                    job=next((j for j in self.jobs if j.id==event[1]),None)
                    if job:
                        job.cover_path=event[2];job.cover_source=event[3];job.cover_mode='auto'
                        if self.operation=='convert' and not self.cancel.is_set():self.follow_current_book(job.id)
                        if self.current==job.id:self.preview_cover()
                        self.refresh_job(job)
                    break  # Paint the applied cover before advancing to another book.
                elif kind=='run_started':
                    self.report_path=event[1];self.progress_log=event[2]
                elif kind in ('status','chapters'):
                    job=next((j for j in self.jobs if j.id==event[1]),None)
                    if job:
                        if kind=='status':job.status=event[2];self.tree.set(job.id,'status',job.status);self.status.set(event[2]);self.tree.item(job.id,text=self.book_text(job))
                        else:job.chapter_count=event[2];self.tree.set(job.id,'chapters',job.chapter_count);self.refresh_job(job)
                elif kind=='progress':
                    if self.operation=='convert':self.show_progress('转换进度',event[1],event[2] if len(event)>2 else self.progress.maximum)
                elif kind=='log':self.log(event[2])
                elif kind=='preview' and event[1]==self.preview_token:
                    self.parsed=event[2];job=self.current_job()
                    if job:
                        job.chapter_count=count_chapters(self.parsed);self.tree.set(job.id,'chapters',job.chapter_count)
                        self.preview_info.set(f'{job.chapter_count} 章 · {self.parsed.encoding} · {self.parsed.characters:,} 字符')
                        self.notes.set('\n'.join(dict.fromkeys(job.notes+self.parsed.warnings)) or '章节识别完成，可预览后转换。')
                        for i,ch in enumerate(self.parsed.chapters):self.chapter_tree.insert('','end',iid=str(i),values=(ch.line,('    ' if ch.level==2 else '')+ch.title))
                        if self.parsed.chapters:self.chapter_tree.selection_set('0')
                elif kind=='preview_error' and event[1]==self.preview_token:
                    self.preview_info.set('识别失败');self.notes.set(event[2]);self.set_text(self.body,event[2])
                elif kind=='covers':
                    self.status.set('封面搜索完成')
                    if not self.closing:self.gallery(event[1],event[2])
                elif kind=='cover_error':
                    self.status.set('封面搜索失败')
                    if not self.closing:messagebox.showwarning('封面搜索失败',event[2])
                elif kind=='done':
                    report=event[1];self.busy=False;self.operation=None;self.lock_controls(False);self.report_path=report['report_path']
                    self.show_progress('已停止' if report['stopped'] else '转换结束',len(report['results']),report['requested'])
                    for result in report['results']:
                        self.log(f'{result["title"]}：{result["status"]}\n'+'\n'.join(result['outputs']))
                        if result.get('warnings'):self.log('提示：'+'；'.join(result['warnings']))
                        if result.get('error'):self.log('错误：'+result['error'])
                        if result.get('error_log'):self.log('错误日志：'+result['error_log'])
                        if result['status']=='完成':
                            job=next((j for j in self.jobs if j.id==result['id']),None)
                            if job:job.selected=False;self.refresh_job(job)
                    done=sum(r['status']=='完成' for r in report['results']);self.status.set(f'完成 {done} / {report["requested"]} 本'+(' · 已停止' if report['stopped'] else ''))
                    self.summarize_conversion_covers(report)
                    self.summary.set(f'{len(self.jobs)} 本 · 已选 {sum(j.selected for j in self.jobs)}');self.update_start();self.persist()
                elif kind=='batch_error':
                    self.busy=False;self.operation=None;self.lock_controls(False);self.show_progress('转换失败',self.progress.value,self.progress.maximum);self.status.set('转换失败');self.log(event[1])
                    if len(event)>2:self.progress_log=event[2];self.log('错误日志：'+event[2])
                    if not self.closing:messagebox.showerror('转换失败',event[1])
        except queue.Empty:pass
        except Exception:logging.exception('处理界面事件失败')
        if self.closing and not self.busy:
            self.finish_close();return
        self.root.after(40 if not self.events.empty() else 100,self.pump)

    def close(self):
        if self.closing:return
        if self.busy:
            if not messagebox.askyesno('任务进行中','停止当前任务并关闭？已导出的电子书会保留。'):return
            self.closing=True;self.stop();return
        self.save_fields();self.persist();self.closing=True;self.finish_close()


    def book_text(self,job):
        status='整理中…' if job.id in self.preparing else (f'{job.chapter_count} 章' if job.chapter_count else job.status)
        return f'{"☑" if job.selected else "☐"}  {job.title}\n     {job.author or "作者未填写"}  ·  {status}'

    def update_start(self):
        waiting=sum(j.selected and j.id in self.preparing for j in self.jobs)
        self.start_button.state(['disabled'] if self.busy or waiting or not any(j.selected for j in self.jobs) else ['!disabled'])
        count=sum(job.selected for job in self.jobs)
        self.start_button.label=f'开始转换 · {count} 本';self.start_button.draw()
        if hasattr(self,'remove_selected_button'):
            self.remove_selected_button.label='移除选中';self.remove_selected_button.draw()
            self.remove_selected_button.state(['disabled'] if self.busy or not count else ['!disabled'])
            self.select_all_button.state(['disabled'] if self.busy or not self.jobs or count==len(self.jobs) else ['!disabled'])
            self.select_none_button.state(['disabled'] if self.busy or not count else ['!disabled'])
        if hasattr(self,'prepare_all_button'):self.prepare_all_button.state(['disabled'] if self.busy or not self.jobs else ['!disabled'])
        if hasattr(self,'organize_all_button'):self.organize_all_button.state(['disabled'] if self.busy or self.preparing or not self.jobs else ['!disabled'])
        for button in getattr(self,'outline_buttons',[]):button.state(['disabled'] if self.busy or not self.current_job() or self.current in self.preparing or self.parsed is None else ['!disabled'])
        selected=self.chapter_tree.selection()
        if getattr(self,'outline_buttons',None) and selected and self.outline_nodes.get(selected[0],{}).get('kind')!='chapter':self.outline_buttons[0].state(['disabled'])
        if hasattr(self,'empty_queue'):
            if self.jobs:self.empty_queue.place_forget();self.empty_queue.lower()
            else:self.empty_queue.place(relx=.5,rely=.44,anchor='center');self.empty_queue.lift()
        if hasattr(self,'summary'):self.summary.set(f'{len(self.jobs)} 本 · 已选 {count}')

    def queue_menu(self,event):
        item=self.tree.identify_row(event.y)
        if item:self.tree.selection_set(item)
        selected=sum(job.selected for job in self.jobs)
        menu=tk.Menu(self.root,tearoff=False)
        menu.add_command(label=f'移出已勾选的 {selected} 本',command=self.remove_selected,state='normal' if selected and not self.busy else 'disabled')
        if item:menu.add_command(label='仅移出当前书籍',command=lambda:self.remove_jobs({item}),state='disabled' if self.busy else 'normal')
        menu.add_command(label='全选',command=self.select_all,state='disabled' if self.busy else 'normal')
        menu.add_command(label='取消全选',command=self.select_none,state='disabled' if self.busy else 'normal')
        menu.add_separator();menu.add_command(label='清空队列（保留文件）',command=self.clear_queue,state='disabled' if self.busy else 'normal')
        menu.tk_popup(event.x_root,event.y_root)

    def organize_all(self):
        if self.busy or not self.jobs or self.preparing:return
        if self.current and not self.save_fields():messagebox.showwarning('书名为空','请先填写书名');return
        self.busy=True;self.operation='outline';self.cancel.clear();self.outline_run={job.id:None for job in self.jobs}
        self.begin_task_log('outline');self.lock_controls(True);self.workspace_tabs.select(0)
        self.log(f'重新识别目录：{len(self.jobs)} 本 · 不搜索封面')
        self.queue_notice.set('正在重新识别全部目录');self.show_progress('目录识别',0,len(self.jobs))
        for job in self.jobs:
            self.invalidate_analysis(job.id);self.prepare_job(job,refresh=False)
        self.refresh_queue()

    def invalidate_analysis(self,job_id):
        future=self.analysis_futures.pop(job_id,None)
        if future is not None:future.cancel()
        self.analysis_versions[job_id]=self.analysis_versions.get(job_id,0)+1
        self.preparing.discard(job_id);self.import_follow_versions.pop(job_id,None);self.book_cache.pop(job_id,None);self.catalogs.pop(job_id,None)

    def prepare_job(self,job,refresh=True,follow_import=False):
        previous=self.analysis_futures.pop(job.id,None)
        if previous is not None:previous.cancel()
        version=self.analysis_versions.get(job.id,0)+1;self.analysis_versions[job.id]=version;self.preparing.add(job.id);job.status='正在整理目录'
        if follow_import:self.import_follow_versions[job.id]=version
        else:self.import_follow_versions.pop(job.id,None)
        self.book_cache.pop(job.id,None);snapshot=copy.deepcopy(job)
        if refresh:self.refresh_job(job)
        def work():
            while not self.analysis_slots.acquire(timeout=.1):
                if self.analysis_shutdown.is_set():return
            if self.analysis_shutdown.is_set():self.analysis_slots.release();return
            try:
                book=read_job(snapshot);self.preview_store.save(job.id,version,book)
                self.events.put(('analysis_ready',job.id,version,book))
            except Exception as exc:self.events.put(('analysis_error',job.id,version,str(exc)))
        self.analysis_futures[job.id]=self.analysis_pool.submit(work)

    def finish_analysis(self,event):
        followed=False
        try:
            kind,job_id,version,value=event
            if self.analysis_versions.get(job_id)!=version:return False
            self.analysis_futures.pop(job_id,None);self.preparing.discard(job_id);job=next((j for j in self.jobs if j.id==job_id),None)
            follow_version=self.import_follow_versions.pop(job_id,None)
            if not job:return False
            should_follow=(follow_version==version and self.operation in (None,'import') or self.operation=='outline' and job_id in self.outline_run) and not self.cancel.is_set() and not self.closing
            if kind=='analysis_error':
                self.directory_errors[job_id]=value
                job.status='整理失败';job.chapter_count=0;self.catalogs.pop(job_id,None);self.log(job.title+'：'+value)
                if should_follow:followed=self.follow_current_book(job_id,load_preview=False)
                if self.current==job_id:self.notes.set(value);self.preview_info.set('目录整理失败');self.parsed=None;self.chapter_tree.delete(*self.chapter_tree.get_children());self.set_text(self.body,value)
            else:
                self.directory_errors.pop(job_id,None)
                book=value;job.chapter_count=count_chapters(book);job.status='目录已就绪'
                self.catalogs[job_id]=[(c.title,c.line,c.level,len(c.text)) for c in book.chapters]
                self.book_cache[job_id]=book;self.book_cache.move_to_end(job_id)
                while len(self.book_cache)>2:self.book_cache.popitem(last=False)
                if should_follow and self.current!=job_id:
                    followed=self.follow_current_book(job_id)
                elif self.current==job_id:self.display_book(book)
            self.refresh_job(job);self.persist()
            if self.operation=='outline' and job_id in self.outline_run:
                self.outline_run[job_id]=value if kind=='analysis_error' else ''
                self.show_progress('目录识别',sum(result is not None for result in self.outline_run.values()),len(self.outline_run))
                self.log('《'+job.title+'》：'+('目录识别失败 · '+value if kind=='analysis_error' else f'目录已就绪 · {job.chapter_count} 章'))
            if not self.busy:
                failed=sum(item.status=='整理失败' for item in self.jobs)
                self.status.set(f'正在整理 {len(self.preparing)} 本…' if self.preparing else f'已自动整理 {len(self.jobs)-failed} 本书的目录'+(f' · {failed} 本失败' if failed else ''))
                if kind=='analysis_ready' and job_id in self.outline_messages:self.status.set(self.outline_messages.pop(job_id))
            return followed
        finally:
            self.analysis_slots.release()
            self.complete_preparation();self.complete_outline()

    def display_book(self,book):
        from outline import render_outline
        job=self.current_job()
        if job:
            book.metadata.title=job.title;book.metadata.author=job.author
            for chapter in book.chapters:
                if chapter.kind!='volume':chapter.title=job.chapter_titles.get(str(chapter.line),chapter.title)
        render_outline(self,book)

    def show_settings(self):
        from settings_dialog import show_settings
        return show_settings(self)

    def request_covers(self,job_id,title,refresh,deep=False):
        job=next((j for j in self.jobs if j.id==job_id),None);win=self.cover_windows.get(job_id)
        if not job or not win or not win.winfo_exists():return
        for child in win.tiles.winfo_children():child.destroy()
        win.photos=[];win.tile_widgets=[];win.diagnostics=[];win.search_version=object();version=win.search_version;author=job.author;win.message.set('正在检索多个来源…');win.progress.start(15)
        def work():
            try:self.events.put(('cover_result',job_id,version,search_details(title,author,refresh,lambda text:self.events.put(('cover_progress',job_id,version,text)),deep_search=deep)))
            except Exception as exc:self.events.put(('cover_failure',job_id,version,str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def receive_import_job(self,token,job):
        if self.import_token is not token or self.closing:return
        if len(self.jobs)>=MAX_BATCH_FILES or any(item.path==job.path for item in self.jobs):return
        self.jobs.append(job);self.prepare_job(job,refresh=False,follow_import=True);self.refresh_queue()

    def finish_import(self,event):
        _,token,batch,count,errors=event
        if self.import_token is not token:return
        self.importing=False;self.operation=None;self.busy=False;self.lock_controls(False);self.show_progress('待转换')
        message=import_summary(count,batch) if batch is not None else '导入失败'
        if errors:message+=f' · {len(errors)} 本未能导入'
        self.queue_notice.set(message);self.status.set(message);self.log(message)
        for error in errors+(batch.errors if batch is not None else []):self.log(error)
        if self.jobs and not self.current:self.tree.selection_set(self.jobs[0].id)
        if batch is not None and batch.rejected:self.reject_import()
        self.persist()

    def prepare_all(self):
        if self.busy or not self.jobs:return
        if self.current and not self.save_fields():messagebox.showwarning('书名为空','请先填写书名');return
        self.busy=True;self.operation='prepare';self.prepare_report=None;self.cancel.clear();self.begin_task_log('cover-search');self.lock_controls(True)
        self.show_progress('封面刮削',0,len(self.jobs));self.status.set('正在联网搜索缺失封面…')
        self.workspace_tabs.select(0);self.queue_notice.set('搜索缺失封面 · 已有图片保留')
        self.log(f'一键搜索封面：{len(self.jobs)} 本 · 联网搜索、下载并应用；已有图片保留，不重新识别目录')
        snapshot=copy.deepcopy(self.jobs)
        def work():
            try:report=prepare_covers(snapshot,lambda *event:self.events.put(event),self.cancel)
            except Exception as exc:report={'requested':len(snapshot),'results':[],'stopped':self.cancel.is_set(),'error':str(exc)}
            self.events.put(('prepare_done',report))
        self.worker=threading.Thread(target=work,daemon=True);self.worker.start()

    def follow_current_book(self,job_id,load_preview=True):
        if not self.tree.exists(job_id):return False
        self.tree.selection_set(job_id);self.tree.focus(job_id);self.tree.see(job_id)
        self.on_select(load_preview=load_preview)
        return self.current==job_id

    def follow_conversion(self,job_id,index,total):
        if self.operation!='convert' or self.cancel.is_set():return
        self.processing_job_id=job_id
        if self.follow_current_book(job_id):
            self.queue_notice.set(f'正在处理 {index}/{total} · {self.current_job().title}')

    def receive_conversion_book(self,job_id,book):
        if self.operation!='convert':return
        job=next((item for item in self.jobs if item.id==job_id),None)
        if not job:return
        self.book_cache[job_id]=book;self.book_cache.move_to_end(job_id)
        while len(self.book_cache)>2:self.book_cache.popitem(last=False)
        self.catalogs[job_id]=[(c.title,c.line,c.level,len(c.text)) for c in book.chapters]
        if self.current==job_id:self.display_book(book)

    def follow_preparation(self,job_id,index,total):
        if self.operation!='prepare' or self.cancel.is_set():return
        if not self.follow_current_book(job_id):return
        self.queue_notice.set(f'封面刮削 {index}/{total} · {self.current_job().title}')
        self.show_progress('封面刮削',index-1,total)

    def receive_prepared_cover(self,result):
        job=next((job for job in self.jobs if job.id==result['id']),None)
        if not job:return
        row=result['cover'];self.cover_results[job.id]=result
        if row:
            job.cover_path=row.path;job.cover_source=f'{row.provider} · 自动匹配';job.cover_mode='auto'
            if row.match!='书名、作者匹配':
                note='自动采用书名匹配的图片候选，作者请核对'
                if note not in job.notes:job.notes.append(note)
        label='已保留封面' if result['kept'] else '封面已匹配' if row else '封面获取失败' if result['error'] else '未找到匹配封面'
        self.log('《'+job.title+'》：'+label+(' · '+result['error'] if result['error'] else ''))
        self.refresh_job(job)
        if self.current==job.id:
            self.preview_cover()
            if row and row.match!='书名、作者匹配':self.notes.set('\n'.join(job.notes))

    def complete_preparation(self):
        if self.operation!='prepare' or self.prepare_report is None:return
        report=self.prepare_report;self.prepare_report=None
        results={row['id']:row for row in report['results']};missing=[]
        for job in self.jobs:
            if job.cover_path and Path(job.cover_path).is_file():continue
            row=results.get(job.id)
            reason=(row.get('reason') or row.get('error') or '未找到可用的匹配封面') if row else ('搜索任务异常：'+report['error'] if report.get('error') else '已停止，尚未完成搜索' if report.get('stopped') else '未返回搜索结果')
            missing.append({'id':job.id,'title':job.title,'filename':Path(job.path).name,'reason':reason})
            self.cover_results[job.id]=row or {'cover':None,'kept':False,'error':'','reason':reason}
            self.refresh_job(job)
        covered=len(self.jobs)-len(missing)
        message=f'封面搜索完成 · 封面 {covered}/{len(self.jobs)}'+(f' · {len(missing)} 本待补封面' if missing else '')
        if report.get('stopped'):message='已停止 · '+message
        self.queue_notice.set(f'图片封面 {covered}/{len(self.jobs)}'+(f' · 待补 {len(missing)}' if missing else ''))
        self.log(message);self.log_missing_covers(missing)
        serial={**report,'results':[{**row,'cover':asdict(row['cover']) if row['cover'] else None} for row in report['results']],'missing_covers':missing}
        Path(self.report_path).write_text(json.dumps(serial,ensure_ascii=False,indent=2),encoding='utf-8')
        if missing:self.workspace_tabs.select(1)
        self.busy=False;self.operation=None;self.lock_controls(False);self.show_progress('待转换');self.status.set(message);self.persist()

    def select_all(self):
        if self.busy:return
        for job in self.jobs:job.selected=True
        self.refresh_queue()

    def select_none(self):
        if self.busy:return
        for job in self.jobs:job.selected=False
        self.refresh_queue()

    def reject_import(self):
        message='一次最多导入 50 个文件，列表最多容纳 50 本。\n本次未导入，请减少文件数量或拆分文件夹。'
        self.queue_notice.set('无法导入：每次最多 50 本，请减少文件或拆分文件夹')
        self.queue_notice_label.configure(fg='#BF5A58');self.status.set('无法导入：超过 50 本上限，本次未导入')
        messagebox.showwarning('超过导入上限',message,parent=self.root)


    def outline_menu(self,event):
        from outline import menu
        menu(self,event)

    def on_queue_double_click(self,event):
        if self.busy or not 30*self.ui_scale<=event.x<=76*self.ui_scale:return
        item=self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item);self.on_select();self.online_cover()
        return 'break'

    def queue_cover_label(self,job):
        if job.cover_path and Path(job.cover_path).is_file():
            row=self.cover_results.get(job.id,{})
            if row.get('cover') and row['cover'].match!='书名、作者匹配':return '图片待核对'
            return '已匹配图片' if row.get('cover') or '自动匹配' in job.cover_source else '已有图片'
        row=self.cover_results.get(job.id)
        if row:
            return '搜索失败' if row.get('error') else '尚未搜索' if '停止' in row.get('reason','') else '未匹配 · 待补'
        return '指定文字封面' if job.cover_mode=='generated' else '待搜索封面'

    def queue_values(self,job):
        return ('✓' if job.selected else '',job.title,job.author or '—',job.chapter_count or '—',job.status,self.queue_cover_label(job))

    def queue_thumbnail(self,job):
        scale=self.ui_scale;size=(max(1,int(36*scale)),max(1,int(54*scale)))
        path=Path(job.cover_path) if job.cover_path else None
        try:stamp=(path.stat().st_mtime_ns,path.stat().st_size) if path else None
        except OSError:stamp=None
        key=(job.cover_path,stamp,size,job.cover_mode)
        cached=self.queue_thumbnails.get(job.id)
        if cached and cached[0]==key:return cached[1]
        image=Image.new('RGB',size,'#F3F5F7')
        if path and stamp:
            try:
                with open_image(path) as source:
                    source=source.convert('RGB');source.thumbnail(size,Image.Resampling.LANCZOS)
                    image.paste(source,((size[0]-source.width)//2,(size[1]-source.height)//2))
            except Exception:pass
        else:
            draw=ImageDraw.Draw(image);w,h=size
            draw.rectangle((0,0,max(1,int(3*scale)),h),fill='#337D77' if job.cover_mode=='generated' else '#CBD5DF')
            draw.rectangle((w*.2,h*.25,w*.8,h*.29),fill='#ABB8C4')
            draw.rectangle((w*.2,h*.36,w*.65,h*.39),fill='#CBD4DE')
            draw.ellipse((w*.25,h*.72,w*1.35,h*1.25),fill='#D9E5E1')
        photo=ImageTk.PhotoImage(image,master=self.root);self.queue_thumbnails[job.id]=(key,photo)
        return photo

    def receive_cached_preview(self,event):
        kind,job_id,token,version,value=event
        if job_id!=self.current or token!=self.preview_token or version!=self.analysis_versions.get(job_id,0):return
        if kind=='cached_preview_error':
            self.preview_info.set('预览缓存无法读取');self.set_text(self.body,'预览缓存无法读取，请点击“重新识别目录”。\n'+value);return
        self.book_cache[job_id]=value;self.book_cache.move_to_end(job_id)
        while len(self.book_cache)>2:self.book_cache.popitem(last=False)
        self.display_book(value)

    def begin_task_log(self,kind):
        folder=ROOT/'archive'/kind/str(time.time_ns());folder.mkdir(parents=True,exist_ok=True)
        self.report_path=str(folder/'report.json');self.progress_log=str(folder/'progress.log')

    def log_missing_covers(self,missing):
        self.log(f'未完成封面：{len(missing)} 本'+(' · 无待补封面' if not missing else ' · 以下书籍需要手动补图或重新搜索'))
        for row in missing:self.log(f'《{row["title"]}》 · {row["filename"]} · {row["reason"]}')

    def complete_outline(self):
        if self.operation!='outline' or self.preparing:return
        missing=[]
        for job in self.jobs:
            result=self.outline_run.get(job.id)
            if result is None or result:
                missing.append({'title':job.title,'filename':Path(job.path).name,'reason':result or '已停止，尚未识别'})
        message=f'目录识别完成 · {len(self.jobs)-len(missing)}/{len(self.jobs)} 本'
        if self.cancel.is_set():message='已停止 · '+message
        self.log(message);self.log(f'未完成目录：{len(missing)} 本')
        for row in missing:self.log(f'《{row["title"]}》 · {row["filename"]} · {row["reason"]}')
        Path(self.report_path).write_text(json.dumps({'results':self.outline_run,'unfinished':missing,'stopped':self.cancel.is_set()},ensure_ascii=False,indent=2),encoding='utf-8')
        if missing:self.workspace_tabs.select(1)
        self.busy=False;self.operation=None;self.lock_controls(False);self.show_progress('待转换');self.queue_notice.set(message);self.status.set(message)

    def summarize_conversion_covers(self,report):
        missing=[]
        for row in report['results']:
            if row.get('cover_type')=='图片封面' or row.get('cover_source')=='指定文字封面':continue
            reason=next((warning for warning in row.get('warnings',[]) if '封面' in warning),row.get('error') or '未取得图片封面，使用文字封面')
            missing.append({'title':row['title'],'filename':Path(row['input']).name,'reason':reason})
        self.log_missing_covers(missing)
        if self.progress_log:
            with Path(self.progress_log).open('a',encoding='utf-8') as stream:
                stream.write('\n未完成封面：'+str(len(missing))+' 本\n')
                for row in missing:stream.write('《'+row['title']+'》 · '+row['filename']+' · '+row['reason']+'\n')

    def finish_close(self):
        if not self.shutdown_started:
            self.shutdown_started=True;self.analysis_shutdown.set()
            self.status.set('正在关闭并清理临时缓存…')
            if self.maintenance_after is not None:
                try:self.root.after_cancel(self.maintenance_after)
                except tk.TclError:pass
            # Analysis callbacks normally release these slots in pump(). At shutdown
            # no more UI results are consumed, so cancel queued work and wake waiters.
            def finish():
                try:
                    self.analysis_pool.shutdown(wait=True,cancel_futures=True)
                    self.maintenance.finish()
                finally:self.shutdown_ready.set()
            threading.Thread(target=finish,name='zbook-safe-exit',daemon=False).start()
        if self.shutdown_ready.is_set():self.root.destroy()
        else:self.root.after(50,self.finish_close)

    def schedule_maintenance(self):
        if self.closing:return
        protected=[job.cover_path for job in self.jobs if job.cover_path]+[p for p in (self.report_path,self.progress_log) if p]
        for window in self.cover_windows.values():
            try:
                if window.winfo_exists():protected.extend(getattr(window,'cached_paths',[]))
            except tk.TclError:pass
        protected.extend(handler.baseFilename for handler in logging.getLogger().handlers if hasattr(handler,'baseFilename'))
        if not self.busy:self.maintenance.start_sweep(protected)
        self.maintenance_after=self.root.after(30*60*1000,self.schedule_maintenance)

def main():
    if os.name=='nt':
        try:ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:pass
        try:ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('ZBook.Desktop.1')
        except Exception:pass
    (ROOT/'archive'/'logs').mkdir(parents=True,exist_ok=True)
    logging.basicConfig(filename=ROOT/'archive'/'logs'/('application-'+time.strftime('%Y%m%d')+'.log'),level=logging.INFO,encoding='utf8',format='%(asctime)s %(levelname)s %(message)s')
    if '--smoke-test' in __import__('sys').argv:
        from portable_smoke import main as smoke
        try:smoke()
        except Exception:
            logging.exception('便携版验收失败');raise SystemExit(1)
        return
    root=tk.Tk();app=ZBook(root);root.mainloop()

if __name__=='__main__':
    main()
