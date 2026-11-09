# SPDX-License-Identifier: AGPL-3.0-only
"""Small native Windows drop bridge and rounded Tk buttons; no Qt/web runtime."""
import os,ctypes as C
import queue
import logging
from ctypes import wintypes as W
import tkinter as tk
from tkinter import font as tkfont

class SoftButton(tk.Canvas):
    def __init__(self,parent,text='',command=None,primary=False,width=None,height=36,**kw):
        self.label=text;self.command=command;self.primary=primary;self.disabled=False;self.hover=False
        self.scale=float(parent.tk.call('tk','scaling'))/(96/72)
        self._width=int(width*self.scale) if width else max(int(72*self.scale),tkfont.Font(parent,font=('Microsoft YaHei UI',10)).measure(text)+int(28*self.scale))
        super().__init__(parent,width=self._width,height=int(height*self.scale),bg=parent.cget('background') if isinstance(parent,(tk.Frame,tk.Tk)) else '#ffffff',highlightthickness=0,bd=0,takefocus=True,**kw)
        self.bind('<Configure>',lambda e:self.draw());self.bind('<Enter>',lambda e:self._hover(True));self.bind('<Leave>',lambda e:self._hover(False))
        self.bind('<ButtonRelease-1>',lambda e:self.invoke());self.bind('<Button-1>',lambda e:self.focus_set())
        self.bind('<Return>',lambda e:self.invoke());self.bind('<space>',lambda e:self.invoke())
        self.bind('<FocusIn>',lambda e:self.draw());self.bind('<FocusOut>',lambda e:self.draw());self.draw()
    def _hover(self,value):self.hover=value;self.draw()
    def draw(self):
        self.delete('all');w=self.winfo_width() if self.winfo_width()>1 else self._width;h=self.winfo_height() if self.winfo_height()>1 else int(super().cget('height'));r=int(10*self.scale)
        fill=('#c5d6ed' if self.disabled else '#0066df' if self.hover else '#007aff') if self.primary else ('#f3f3f5' if self.disabled else '#e8e9ee' if self.hover else '#f0f1f5')
        points=[r,1,w-r,1,w-1,1,w-1,r,w-1,h-r,w-1,h-1,w-r,h-1,r,h-1,1,h-1,1,h-r,1,r,1,1]
        self.create_polygon(points,smooth=True,splinesteps=24,fill=fill,outline='#8bb7ed' if self.focus_get()==self else fill)
        self.create_text(w/2,h/2,text=self.label,fill='#ffffff' if self.primary else '#a0a2aa' if self.disabled else '#25272d',font=('Microsoft YaHei UI',10))
        self.configure(cursor='arrow' if self.disabled else 'hand2')
    def invoke(self):
        if not self.disabled and self.command:return self.command()
    def state(self,states):
        for state in states:
            if state=='disabled':self.disabled=True
            elif state=='!disabled':self.disabled=False
        self.draw()
    def configure(self,cnf=None,**kw):
        redraw='text' in kw or 'state' in kw
        if 'text' in kw:self.label=kw.pop('text')
        if 'state' in kw:self.disabled=kw.pop('state')=='disabled'
        result=super().configure(cnf,**kw)
        if hasattr(self,'label') and redraw:self.draw()
        return result
    config=configure
    def cget(self,key):return self.label if key=='text' else super().cget(key)

class SlimScrollbar(tk.Canvas):
    """Quiet scroll rail with an always reserved width and a draggable thumb."""
    def __init__(self,parent,command=None,**kw):
        self.scale=float(parent.tk.call('tk','scaling'))/(96/72)
        self.command=command;self.first=0.;self.last=1.;self.drag_origin=None;self.hover=False
        super().__init__(parent,width=max(10,int(12*self.scale)),height=1,bg='#ffffff',highlightthickness=0,bd=0,takefocus=False,**kw)
        self.bind('<Configure>',lambda e:self.draw())
        self.bind('<Enter>',lambda e:self._hover(True));self.bind('<Leave>',lambda e:self._hover(False))
        self.bind('<Button-1>',self.press);self.bind('<B1-Motion>',self.drag);self.bind('<ButtonRelease-1>',self.release)
        self.bind('<MouseWheel>',self.wheel)
    def set(self,first,last):
        self.first=max(0.,min(1.,float(first)));self.last=max(self.first,min(1.,float(last)));self.draw()
    def get(self):return self.first,self.last
    def _hover(self,value):self.hover=value;self.draw()
    def geometry_values(self):
        inset=max(2,int(3*self.scale));track=max(1,self.winfo_height()-2*inset)
        span=self.last-self.first;size=min(track,max(int(28*self.scale),track*span))
        travel=track-size;top=inset+(travel*self.first/(1-span) if span<1 else 0)
        return inset,track,size,travel,top
    def draw(self):
        self.delete('all')
        if self.last-self.first>=.9999:return
        inset,track,size,travel,top=self.geometry_values();w=self.winfo_width();x=max(3,w*.3);right=w-x;r=min((right-x)/2,size/2)
        fill='#999fa9' if self.hover or self.drag_origin else '#c7cbd2'
        self.create_rectangle(x,top+r,right,top+size-r,fill=fill,outline='')
        self.create_oval(x,top,right,top+2*r,fill=fill,outline='')
        self.create_oval(x,top+size-2*r,right,top+size,fill=fill,outline='')
    def press(self,event):
        if not self.command or self.last-self.first>=1:return
        _,_,size,_,top=self.geometry_values()
        if top<=event.y<=top+size:self.drag_origin=(event.y,self.first)
        else:self.command('scroll',-1 if event.y<top else 1,'pages')
        self.draw()
    def drag(self,event):
        if self.drag_origin is None or not self.command:return
        _,_,_,travel,_=self.geometry_values();span=self.last-self.first
        fraction=self.drag_origin[1]+(event.y-self.drag_origin[0])*max(0.,1-span)/max(1,travel)
        self.command('moveto',max(0.,min(1-span,fraction)))
    def release(self,event):self.drag_origin=None;self.draw()
    def wheel(self,event):
        target=getattr(self.command,'__self__',None)
        if target is not None and hasattr(target,'wheel'):return target.wheel(event)
        if self.command:self.command('scroll',-int(event.delta/120) if abs(event.delta)>=120 else (-1 if event.delta>0 else 1),'units')
        return 'break'


class NativeFileDrop:
    """Queue WM_DROPFILES without entering Tcl from a native WNDPROC callback."""
    MAX_FILES=50
    POLL_MS=40

    def __init__(self,root,widgets,on_files,on_limit=None):
        self.root=root;self.hooks=[];self.on_files=on_files;self.on_limit=on_limit
        self.pending=queue.Queue(maxsize=8);self.closed=False;self.poll_id=None
        if os.name!='nt':return
        self.user=C.WinDLL('user32',use_last_error=True);self.shell=C.WinDLL('shell32',use_last_error=True)
        self.shell.DragAcceptFiles.argtypes=[W.HWND,W.BOOL];self.shell.DragAcceptFiles.restype=None
        self.shell.DragQueryFileW.argtypes=[W.HANDLE,W.UINT,W.LPWSTR,W.UINT];self.shell.DragQueryFileW.restype=W.UINT
        self.shell.DragFinish.argtypes=[W.HANDLE];self.shell.DragFinish.restype=None
        self.user.IsWindow.argtypes=[W.HWND];self.user.IsWindow.restype=W.BOOL
        self.proc_type=C.WINFUNCTYPE(C.c_ssize_t,W.HWND,W.UINT,W.WPARAM,W.LPARAM)
        self.set_proc=self.user.SetWindowLongPtrW if C.sizeof(C.c_void_p)==8 else self.user.SetWindowLongW
        self.set_proc.argtypes=[W.HWND,C.c_int,C.c_void_p];self.set_proc.restype=C.c_void_p
        self.user.CallWindowProcW.argtypes=[C.c_void_p,W.HWND,W.UINT,W.WPARAM,W.LPARAM];self.user.CallWindowProcW.restype=C.c_ssize_t
        root.update_idletasks()
        # Register only the supplied drop surfaces, never the root or its wrapper.
        for handle in dict.fromkeys(int(widget.winfo_id()) for widget in widgets):self.install(handle)
        self.root.bind('<Destroy>',self._destroy,add='+')
        self.poll_id=self.root.after(self.POLL_MS,self._poll)

    def _enqueue(self,kind,value=None):
        if self.closed:return
        try:self.pending.put_nowait((kind,value))
        except queue.Full:pass  # Keep native callbacks bounded when drops arrive repeatedly.

    def _receive(self,handle):
        # No Tk calls here, including after(), event_generate(), or winfo_*().
        try:
            if self.closed:return
            count=self.shell.DragQueryFileW(handle,0xffffffff,None,0)
            if count>self.MAX_FILES:self._enqueue('limit');return
            paths=[]
            for index in range(count):
                length=self.shell.DragQueryFileW(handle,index,None,0)
                if not 0<length<=32767:raise ValueError('拖入文件的路径无效或过长')
                buffer=C.create_unicode_buffer(length+1)
                copied=self.shell.DragQueryFileW(handle,index,buffer,length+1)
                if copied!=length:raise ValueError('未能读取完整的拖入文件路径')
                paths.append(buffer.value)
            if paths:self._enqueue('files',paths)
        except Exception as exc:self._enqueue('error',str(exc))
        finally:self.shell.DragFinish(handle)

    def install(self,handle):
        original=[None]
        @self.proc_type
        def proc(hwnd,msg,wparam,lparam):
            if msg==0x0233:
                self._receive(wparam)
                return 0
            return self.user.CallWindowProcW(original[0],hwnd,msg,wparam,lparam)
        C.set_last_error(0)
        old=self.set_proc(handle,-4,C.cast(proc,C.c_void_p));original[0]=old
        if not old and C.get_last_error():raise C.WinError(C.get_last_error())
        self.shell.DragAcceptFiles(handle,True);self.hooks.append((handle,old,proc))

    def _poll(self):
        self.poll_id=None
        if self.closed:return
        try:
            for _ in range(4):
                try:kind,value=self.pending.get_nowait()
                except queue.Empty:break
                try:
                    if kind=='files':self.on_files(value)
                    elif kind=='limit' and self.on_limit is not None:self.on_limit()
                    elif kind=='error':logging.getLogger(__name__).warning('读取拖入文件失败：%s',value)
                except Exception:logging.getLogger(__name__).exception('处理拖入文件失败')
        finally:
            if not self.closed:self.poll_id=self.root.after(self.POLL_MS,self._poll)

    def _destroy(self,event):
        if event.widget is self.root:self.close()

    def close(self):
        if self.closed:return
        self.closed=True
        if self.poll_id is not None:
            try:self.root.after_cancel(self.poll_id)
            except tk.TclError:pass
            self.poll_id=None
        for handle,old,proc in self.hooks:
            if self.user.IsWindow(handle):
                self.shell.DragAcceptFiles(handle,False)
                self.set_proc(handle,-4,C.c_void_p(old))
        # Retain callback references until the bridge itself is released, even
        # if close() runs while an original window procedure is still unwinding.

