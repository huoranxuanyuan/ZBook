# SPDX-License-Identifier: AGPL-3.0-only
"""Transactional settings and parent-relative placement for ZBook."""
from pathlib import Path
import ctypes
from ctypes import wintypes
import re
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
from batch import find_converter
from branding import APP_NAME,VERSION,AUTHOR
from reading_style import LINE_HEIGHT,PARAGRAPH_GAP
from ui_widgets import SlimScrollbar,SoftButton
from layout_widgets import SmoothScrollCanvas

COVER_STYLES={'现代青绿':'modern','暖调暮橙':'warm'}
RULES=('自动（中文 / 英文）','数字标题','自定义正则')
ENCODINGS=('自动','utf-8','gb18030','big5','utf-16','utf-16le','utf-16be')


def work_area(parent):
    try:
        class MonitorInfo(ctypes.Structure):
            _fields_=[('cbSize',wintypes.DWORD),('rcMonitor',wintypes.RECT),('rcWork',wintypes.RECT),('dwFlags',wintypes.DWORD)]
        user=ctypes.windll.user32
        user.MonitorFromWindow.argtypes=[wintypes.HWND,wintypes.DWORD];user.MonitorFromWindow.restype=wintypes.HANDLE
        user.GetMonitorInfoW.argtypes=[wintypes.HANDLE,ctypes.POINTER(MonitorInfo)];user.GetMonitorInfoW.restype=wintypes.BOOL
        monitor=user.MonitorFromWindow(parent.winfo_id(),2);info=MonitorInfo();info.cbSize=ctypes.sizeof(info)
        if user.GetMonitorInfoW(monitor,ctypes.byref(info)):
            rect=info.rcWork;return rect.left,rect.top,rect.right,rect.bottom
    except (AttributeError,OSError):pass
    return 0,0,parent.winfo_screenwidth(),parent.winfo_screenheight()


def centered_geometry(parent,width,height,area=None):
    left,top,right,bottom=area or work_area(parent)
    width=min(int(width),max(320,right-left-32));height=min(int(height),max(300,bottom-top-32))
    x=parent.winfo_rootx()+(parent.winfo_width()-width)//2
    y=parent.winfo_rooty()+(parent.winfo_height()-height)//2
    x=max(left,min(x,right-width));y=max(top,min(y,bottom-height))
    return width,height,x,y


def validate_converter(value):
    value=value.strip()
    if not value:return ''
    path=Path(value)
    if not path.is_file():raise ValueError('编码器文件不存在，请重新选择或使用内置编码器。')
    if path.name.lower() not in ('kindling.exe','kindling-cli-windows.exe','kindlegen.exe','ebook-convert.exe'):
        raise ValueError('请选择 kindling.exe、kindlegen.exe 或 Calibre 的 ebook-convert.exe。')
    return str(path.resolve())


class SettingsDialog:
    def __init__(self,ui,show=True):
        self.ui=ui;self.target=ui.current_job();self.scale=ui.ui_scale
        self.window=tk.Toplevel(ui.root);win=self.window;win.withdraw();win.title('ZBook 设置');win.configure(bg='#FFFFFF');win.transient(ui.root)
        self.encoding=tk.StringVar(win,value=self.target.encoding if self.target else '自动')
        self.rule=tk.StringVar(win,value=self.target.rule if self.target else RULES[0])
        self.regex=tk.StringVar(win,value=self.target.custom_regex if self.target else '')
        self.line_height=tk.StringVar(win,value=ui.line_height.get());self.gap=tk.StringVar(win,value=ui.gap.get())
        self.auto_cover=tk.BooleanVar(win,value=ui.auto_cover.get());self.converter=tk.StringVar(win,value=ui.converter.get())
        style=ui.settings.get('cover_style','modern');self.cover_style=tk.StringVar(win,value=next((name for name,value in COVER_STYLES.items() if value==style),'现代青绿'))
        self.engine_text=tk.StringVar(win);self.controls={};self.help_labels=[]
        header=tk.Frame(win,bg='#FFFFFF');header.pack(fill='x',padx=24,pady=(20,12))
        tk.Label(header,text='设置',bg='#FFFFFF',fg='#243247',font=('Microsoft YaHei UI',17,'bold')).pack(anchor='w')
        tk.Label(header,text='识别、阅读排版与转换偏好',bg='#FFFFFF',fg='#8993A3',font=('Microsoft YaHei UI',9)).pack(anchor='w',pady=(5,0))
        footer=tk.Frame(win,bg='#FFFFFF');footer.pack(side='bottom',fill='x',padx=24,pady=(12,18))
        self.apply_button=ui.button(footer,'保存设置',self.apply,pack={'side':'right'},managed=False,primary=True)
        self.cancel_button=ui.button(footer,'取消',self.cancel,pack={'side':'right','padx':(0,8)},managed=False)
        container=tk.Frame(win,bg='#FFFFFF');container.pack(fill='both',expand=True,padx=24)
        container.rowconfigure(0,weight=1);container.columnconfigure(0,weight=1)
        self.canvas=SmoothScrollCanvas(container,bg='#FFFFFF',highlightthickness=0,width=1,height=1)
        scroll=SlimScrollbar(container,command=self.canvas.yview);self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.grid(row=0,column=0,sticky='nsew');scroll.grid(row=0,column=1,sticky='ns')
        self.body=tk.Frame(self.canvas,bg='#FFFFFF');self.body.columnconfigure(1,weight=1)
        self.body_window=self.canvas.create_window((0,0),window=self.body,anchor='nw')
        self.body.bind('<Configure>',lambda event:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>',self.resize)
        win.bind('<MouseWheel>',self.canvas.wheel)
        row=0
        self.section(row,'书籍识别');row+=1
        hint='仅作用于当前书籍：'+self.target.title if self.target else '先选择一本书，再修改识别设置。'
        self.hint(row,hint);row+=1
        self.controls['encoding']=self.combo(row,'TXT 编码',self.encoding,ENCODINGS);row+=1
        if not self.target or Path(self.target.path).suffix.lower()!='.txt':self.controls['encoding'].configure(state='disabled')
        self.controls['rule']=self.combo(row,'章节规则',self.rule,RULES);row+=1
        self.controls['regex']=self.entry(row,'自定义正则',self.regex);row+=1
        self.hint(row,'自定义规则匹配完整章节标题；原文中的分卷仍自动识别。');row+=1
        self.rule.trace_add('write',lambda *args:self.update_rule_state());self.update_rule_state()
        if not self.target:self.controls['rule'].configure(state='disabled')
        self.section(row,'阅读与封面');row+=1
        self.controls['line_height']=self.entry(row,'正文行距',self.line_height);row+=1
        self.controls['gap']=self.entry(row,'段距（em）',self.gap);row+=1
        self.controls['cover_style']=self.combo(row,'文字封面',self.cover_style,tuple(COVER_STYLES));row+=1
        self.hint(row,'两种风格均带彩色设计，大字与明暗层次也适合彩色、黑白电纸书。');row+=1
        self.reset_button=SoftButton(self.body,text='恢复默认排版',command=self.reset_reading)
        self.reset_button.grid(row=row,column=1,sticky='w',pady=5);row+=1
        self.controls['auto_cover']=ttk.Checkbutton(self.body,text='转换前自动匹配缺失封面（推荐）',variable=self.auto_cover)
        self.controls['auto_cover'].grid(row=row,column=0,columnspan=2,sticky='w',pady=(10,4));row+=1
        self.hint(row,'开启后会把书名、作者发送至多个公开书籍/图片站点，不上传正文。可关闭自动检索；手动搜封面仍会联网。');row+=1
        self.section(row,'MOBI 编码器');row+=1
        self.engine_label=tk.Label(self.body,textvariable=self.engine_text,bg='#FFFFFF',fg='#67778D',font=('Microsoft YaHei UI',9),anchor='w',justify='left')
        self.engine_label.grid(row=row,column=0,columnspan=2,sticky='ew',pady=(0,8));self.help_labels.append(self.engine_label);row+=1
        engine_actions=tk.Frame(self.body,bg='#FFFFFF');engine_actions.grid(row=row,column=0,columnspan=2,sticky='ew')
        self.external_button=ui.button(engine_actions,'选择外部编码器',self.choose_external,managed=False)
        self.builtin_button=ui.button(engine_actions,'使用内置编码器',self.use_builtin,managed=False)
        self.converter.trace_add('write',lambda *args:self.refresh_engine());self.refresh_engine()
        row+=1
        self.section(row,'关于 ZBook');row+=1
        self.hint(row,f'{APP_NAME} {VERSION} · 作者 {AUTHOR}');row+=1
        self.hint(row,'原创部分采用 AGPL-3.0-only；第三方署名与许可见随附文档。');row+=1
        win.protocol('WM_DELETE_WINDOW',self.cancel);win.bind('<Escape>',lambda event:self.cancel())
        ui.root.update_idletasks()
        self.placement=centered_geometry(ui.root,int(620*self.scale),int(740*self.scale))
        width,height,x,y=self.placement;win.geometry(f'{width}x{height}{x:+d}{y:+d}')
        if show:win.deiconify();win.update_idletasks();win.grab_set();win.lift();win.focus_set()

    def section(self,row,text):
        tk.Label(self.body,text=text,bg='#FFFFFF',fg='#34465F',font=('Microsoft YaHei UI',10,'bold')).grid(row=row,column=0,columnspan=2,sticky='w',pady=(16 if row else 0,9))
    def hint(self,row,text):
        label=tk.Label(self.body,text=text,bg='#FFFFFF',fg='#8A95A6',font=('Microsoft YaHei UI',9),justify='left',anchor='w',wraplength=int(540*self.scale))
        label.grid(row=row,column=0,columnspan=2,sticky='ew',pady=(0,8));self.help_labels.append(label)
    def entry(self,row,label,variable):
        tk.Label(self.body,text=label,bg='#FFFFFF',fg='#62728A',font=('Microsoft YaHei UI',10),width=12,anchor='w').grid(row=row,column=0,sticky='w',pady=5)
        widget=ttk.Entry(self.body,textvariable=variable,width=1);widget.grid(row=row,column=1,sticky='ew',pady=5,padx=(0,8));return widget
    def combo(self,row,label,variable,values):
        tk.Label(self.body,text=label,bg='#FFFFFF',fg='#62728A',font=('Microsoft YaHei UI',10),width=12,anchor='w').grid(row=row,column=0,sticky='w',pady=5)
        widget=ttk.Combobox(self.body,textvariable=variable,values=values,state='readonly',width=1);widget.grid(row=row,column=1,sticky='ew',pady=5,padx=(0,8));return widget
    def resize(self,event):
        self.canvas.itemconfigure(self.body_window,width=event.width)
        for label in self.help_labels:label.configure(wraplength=max(80,event.width-int(12*self.scale)))
    def update_rule_state(self):
        self.controls['regex'].configure(state='normal' if self.target and self.rule.get()=='自定义正则' else 'disabled')
    def reset_reading(self):
        self.line_height.set(f'{LINE_HEIGHT:g}');self.gap.set(f'{PARAGRAPH_GAP:g}')
    def refresh_engine(self):
        value=self.converter.get().strip()
        if value:
            path=Path(value)
            if path.is_file() and path.name.lower() in ('kindling.exe','kindling-cli-windows.exe','kindlegen.exe','ebook-convert.exe'):self.engine_text.set(('外部 Kindling' if 'kindling' in path.name.lower() else '外部 KindleGen' if path.name.lower()=='kindlegen.exe' else '外部 Calibre')+'\n'+str(path))
            else:self.engine_text.set('外部编码器路径已失效，请重新选择或恢复内置。')
        else:
            engine=find_converter('');self.engine_text.set('当前使用内置 Kindling（MOBI7 + KF8）' if engine and Path(engine).name.lower()=='kindling.exe' else '当前使用默认 Calibre' if engine else '未找到可用编码器')
    def choose_external(self):
        path=filedialog.askopenfilename(title='选择 Kindling / KindleGen / Calibre 编码器',parent=self.window,filetypes=[('电子书编码器','*.exe')])
        if not path:return
        try:value=validate_converter(path)
        except ValueError as exc:messagebox.showerror('无法使用编码器',str(exc),parent=self.window);return
        self.converter.set(value)
    def use_builtin(self):self.converter.set('')
    def apply(self):
        try:
            try:line_height=float(self.line_height.get());gap=float(self.gap.get())
            except (ValueError,TypeError) as exc:raise ValueError('行距和段距请输入有效数字。') from exc
            if not .9<=line_height<=3 or not 0<=gap<=3:raise ValueError('行距需为 0.9～3，段距需为 0～3。')
            if self.target and self.rule.get()=='自定义正则':
                if not self.regex.get().strip():raise ValueError('请填写自定义章节正则。')
                if len(self.regex.get())>300:raise ValueError('自定义正则最多 300 个字符。')
                try:
                    from safe_regex import HeadingPattern
                    HeadingPattern(self.regex.get())
                except re.error as exc:raise ValueError('自定义正则无效：'+str(exc)) from exc
            converter=validate_converter(self.converter.get())
        except (ValueError,TypeError) as exc:
            messagebox.showerror('设置未保存',str(exc),parent=self.window);return False
        target=self.target
        if target is not None:
            changed=(target.encoding,target.rule,target.custom_regex)
            if self.ui.current_job() is target:self.ui.save_fields()
            target.encoding=self.encoding.get() if Path(target.path).suffix.lower()=='.txt' else target.encoding
            target.rule=self.rule.get();target.custom_regex=self.regex.get()
            self.ui.encoding.set(target.encoding);self.ui.rule.set(target.rule);self.ui.regex.set(target.custom_regex)
            if changed!=(target.encoding,target.rule,target.custom_regex) or target.id not in self.ui.catalogs:
                self.ui.invalidate_analysis(target.id);self.ui.prepare_job(target)
        self.ui.line_height.set(f'{line_height:g}');self.ui.gap.set(f'{gap:g}');self.ui.auto_cover.set(self.auto_cover.get());self.ui.converter.set(converter)
        self.ui.settings['cover_style']=COVER_STYLES[self.cover_style.get()]
        self.ui.preview_cover();self.ui.persist();self.cancel();return True
    def cancel(self):
        if not self.window.winfo_exists():return
        if self.window.grab_current() is self.window:self.window.grab_release()
        self.window.destroy()


def show_settings(ui):
    if ui.busy:return
    previous=getattr(ui,'settings_dialog',None)
    if previous is not None and previous.window.winfo_exists():previous.window.lift();previous.window.focus_set();return previous
    ui.settings_dialog=SettingsDialog(ui);return ui.settings_dialog
