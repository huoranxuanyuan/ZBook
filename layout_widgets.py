# SPDX-License-Identifier: AGPL-3.0-only
"""Quiet surfaces, segmented navigation and short explanatory tooltips."""
import tkinter as tk
from tkinter import font as tkfont


def rounded(canvas,box,radius,**options):
    x,y,right,bottom=box;r=radius
    return canvas.create_polygon([x+r,y,right-r,y,right,y,right,y+r,right,bottom-r,right,bottom,right-r,bottom,x+r,bottom,x,bottom,x,bottom-r,x,y+r,x,y],smooth=True,splinesteps=24,**options)


class Surface(tk.Canvas):
    def __init__(self,parent,scale=1,padding=16,expand=False,height=100,**options):
        self.scale=scale;self.padding=int(padding*scale);self.expand_body=expand
        super().__init__(parent,bg='#F4F5F7',bd=0,highlightthickness=0,height=int(height*scale),**options)
        self.body=tk.Frame(self,bg='#FFFFFF')
        self.window=self.create_window((self.padding,self.padding),window=self.body,anchor='nw')
        self.bind('<Configure>',self.layout)
        if not expand:self.body.bind('<Configure>',self.fit)
    def fit(self,event=None):
        requested=self.body.winfo_reqheight()+2*self.padding
        if int(self.cget('height'))!=requested:self.configure(height=requested)
    def layout(self,event=None):
        width=max(1,self.winfo_width());height=max(1,self.winfo_height())
        self.delete('surface')
        rounded(self,(1,1,width-1,height-1),int(14*self.scale),fill='#FFFFFF',outline='#E6E9EE',width=1,tags='surface')
        self.tag_lower('surface');self.itemconfigure(self.window,width=max(1,width-2*self.padding))
        if self.expand_body:self.itemconfigure(self.window,height=max(1,height-2*self.padding))


class SegmentedControl(tk.Canvas):
    def __init__(self,parent,labels,command,scale=1):
        self.labels=labels;self.command=command;self.scale=scale;self.selected=0
        self.face=tkfont.Font(parent,font=('Microsoft YaHei UI',10))
        self.segment=max(self.face.measure(label) for label in labels)+int(34*scale)
        super().__init__(parent,width=self.segment*len(labels)+6,height=int(36*scale),bg='#FFFFFF',highlightthickness=0,bd=0,takefocus=True,cursor='hand2')
        self.bind('<Configure>',lambda event:self.draw());self.bind('<Button-1>',self.click)
        self.bind('<Left>',lambda event:self.select(max(0,self.selected-1)))
        self.bind('<Right>',lambda event:self.select(min(len(self.labels)-1,self.selected+1)))
        self.bind('<FocusIn>',lambda event:self.draw());self.bind('<FocusOut>',lambda event:self.draw());self.draw()
    def draw(self):
        self.delete('all');height=int(self.cget('height'));pad=int(3*self.scale)
        for index,label in enumerate(self.labels):
            center=pad+(index+.5)*self.segment
            self.create_text(center,height/2-int(3*self.scale),text=label,font=self.face,fill='#2869BF' if index==self.selected else '#8993A3')
            if index==self.selected:self.create_line(pad+index*self.segment+int(12*self.scale),height-2,pad+(index+1)*self.segment-int(12*self.scale),height-2,fill='#4A83D1',width=max(2,int(2*self.scale)))
    def select(self,index):
        self.selected=index;self.draw();self.command(index)
    def click(self,event):
        self.focus_set();self.select(max(0,min(len(self.labels)-1,int(event.x/self.segment))))


class Tooltip:
    def __init__(self,widget,text):
        self.widget=widget;self.text=text;self.timer=None;self.window=None
        widget.bind('<Enter>',self.schedule,add='+');widget.bind('<Leave>',self.hide,add='+')
        widget.bind('<FocusIn>',self.schedule,add='+');widget.bind('<FocusOut>',self.hide,add='+')
        widget.bind('<ButtonPress>',self.hide,add='+');widget.bind('<Destroy>',self.hide,add='+')
    def schedule(self,event=None):
        self.hide();self.timer=self.widget.after(550,self.show)
    def show(self):
        self.timer=None
        if not self.widget.winfo_exists():return
        self.window=tk.Toplevel(self.widget);self.window.overrideredirect(True)
        self.window.configure(bg='#DDE2E9')
        tk.Label(self.window,text=self.text() if callable(self.text) else self.text,bg='#FFFFFF',fg='#44546A',font=('Microsoft YaHei UI',9),padx=10,pady=6).pack(padx=1,pady=1)
        self.window.geometry(f'+{self.widget.winfo_rootx()}+{self.widget.winfo_rooty()+self.widget.winfo_height()+6}')
    def hide(self,event=None):
        if self.timer is not None:
            try:self.widget.after_cancel(self.timer)
            except tk.TclError:pass
            self.timer=None
        if self.window is not None:
            try:self.window.destroy()
            except tk.TclError:pass
            self.window=None


class ProgressMeter(tk.Canvas):
    def __init__(self,parent,width=112,scale=1):
        self.value=0.;self.maximum=100.;self.scale=scale
        super().__init__(parent,width=int(width*scale),height=max(5,int(6*scale)),bg='#FFFFFF',bd=0,highlightthickness=0)
        self.bind('<Configure>',lambda event:self.draw())
    def draw(self):
        self.delete('all');width=max(1,self.winfo_width());height=max(1,self.winfo_height())
        rounded(self,(0,0,width,height),height/2,fill='#EDF1F6',outline='')
        fraction=max(0.,min(1.,self.value/max(1.,self.maximum)))
        if fraction:rounded(self,(0,0,max(height,width*fraction),height),height/2,fill='#4D88D6',outline='')
    def configure(self,cnf=None,**options):
        changed=False
        for name in ('value','maximum'):
            if name in options:setattr(self,name,float(options.pop(name)));changed=True
        result=super().configure(cnf,**options)
        if changed:self.draw()
        return result
    config=configure



class FormatSwitch(tk.Canvas):
    """Three immediate export choices, with the same quiet surface as buttons."""
    def __init__(self,parent,variable,values,scale=1):
        self.variable=variable;self.values=tuple(values);self.scale=scale
        self.disabled=False;self.hover=None
        self.face=tkfont.Font(parent,font=('Microsoft YaHei UI',10))
        self.widths=[max(int(62*scale),self.face.measure(value)+int(24*scale)) for value in self.values]
        self.inset=max(3,int(3*scale));width=sum(self.widths)+self.inset*2
        super().__init__(parent,width=width,height=int(36*scale),bg='#FFFFFF',
                         bd=0,highlightthickness=0,takefocus=True,cursor='hand2')
        self.trace=self.variable.trace_add('write',lambda *args:self.draw())
        self.bind('<Configure>',lambda event:self.draw())
        self.bind('<Button-1>',self.click)
        self.bind('<Motion>',self.motion)
        self.bind('<Leave>',lambda event:self.motion(None))
        self.bind('<Left>',lambda event:self.step(-1))
        self.bind('<Right>',lambda event:self.step(1))
        self.bind('<Home>',lambda event:self.choose(0))
        self.bind('<End>',lambda event:self.choose(len(self.values)-1))
        self.bind('<FocusIn>',lambda event:self.draw())
        self.bind('<FocusOut>',lambda event:self.draw())
        self.bind('<Destroy>',self.dispose)
        self.draw()

    def current(self):
        try:return self.values.index(self.variable.get())
        except ValueError:return 0

    def choose(self,index):
        if self.disabled:return 'break'
        index=max(0,min(len(self.values)-1,index))
        if self.variable.get()!=self.values[index]:
            self.variable.set(self.values[index]);self.event_generate('<<FormatChanged>>')
        return 'break'

    def step(self,delta):return self.choose(self.current()+delta)

    def hit(self,x):
        edge=self.inset
        for index,width in enumerate(self.widths):
            edge+=width
            if x<edge:return index
        return len(self.values)-1

    def click(self,event):
        if not self.disabled:self.focus_set();self.choose(self.hit(event.x))
        return 'break'

    def motion(self,event):
        self.hover=self.hit(event.x) if event and not self.disabled else None;self.draw()

    def state(self,states):
        for state in states:
            if state=='disabled':self.disabled=True
            elif state=='!disabled':self.disabled=False
        self.configure(takefocus=not self.disabled,cursor='arrow' if self.disabled else 'hand2')
        self.draw()

    def draw(self):
        self.delete('all');height=int(self.cget('height'));width=sum(self.widths)+2*self.inset
        focused=self.focus_get()==self and not self.disabled
        rounded(self,(1,1,width-1,height-1),int(10*self.scale),fill='#F0F1F5',
                outline='#8BB7ED' if focused else '#F0F1F5')
        left=self.inset
        for index,(value,span) in enumerate(zip(self.values,self.widths)):
            selected=index==self.current()
            if selected or self.hover==index:
                rounded(self,(left,self.inset,left+span,height-self.inset),int(8*self.scale),
                        fill='#F7F7F9' if self.disabled else '#FFFFFF' if selected else '#E8EBF0',
                        outline='#E0E5ED' if selected and not self.disabled else '')
            self.create_text(left+span/2,height/2,text=value,font=self.face,
                             fill='#A0A2AA' if self.disabled else '#2869BF' if selected else '#67778D')
            left+=span

    def dispose(self,event):
        if event.widget==self:
            try:self.variable.trace_remove('write',self.trace)
            except tk.TclError:pass


class QuietPanedWindow(tk.PanedWindow):
    """A fine divider over a generous, draggable sash hit area."""
    def __init__(self,parent,scale=1,**options):
        self.scale=scale;self.hover=False;self.drag_origin=None
        super().__init__(parent,orient='horizontal',bg='#FFFFFF',bd=0,
                         sashwidth=max(12,int(12*scale)),sashpad=0,sashrelief='flat',
                         showhandle=False,opaqueresize=True,**options)
        self.divider=tk.Canvas(self,bg='#FFFFFF',bd=0,highlightthickness=0,
                               takefocus=False,cursor='sb_h_double_arrow')
        self.divider.bind('<Enter>',lambda event:self.highlight(True))
        self.divider.bind('<Leave>',lambda event:self.highlight(False))
        self.divider.bind('<Configure>',lambda event:self.draw_divider())
        self.divider.bind('<Button-1>',self.press)
        self.divider.bind('<B1-Motion>',self.drag)
        self.divider.bind('<ButtonRelease-1>',self.release)
        self.bind('<Configure>',lambda event:self.after_idle(self.place_divider),add='+')

    def sash_place(self,index,x,y):
        result=super().sash_place(index,x,y)
        self.place_divider()
        return result

    def place_divider(self):
        if len(self.panes())<2:return
        x,_=self.sash_coord(0)
        self.divider.place(x=x,y=0,width=int(self.cget('sashwidth')),relheight=1)
        tk.Misc.lift(self.divider);self.draw_divider()

    def highlight(self,value):self.hover=value;self.draw_divider()

    def draw_divider(self):
        self.divider.delete('all');width=self.divider.winfo_width();height=self.divider.winfo_height()
        x=width/2;active=self.hover or self.drag_origin is not None
        self.divider.create_line(x,0,x,height,fill='#E7EBF1',width=1)
        grip=int((44 if active else 30)*self.scale)
        rounded(self.divider,(x-max(1,self.scale),max(3,(height-grip)/2),
                             x+max(1,self.scale),min(height-3,(height+grip)/2)),
                max(1,self.scale),fill='#7E9CC5' if active else '#C9D2DE',outline='')

    def press(self,event):
        self.drag_origin=(event.x_root,self.sash_coord(0)[0]);self.draw_divider()

    def drag(self,event):
        if self.drag_origin is None:return
        panes=self.panes()
        minimum=int(self.panecget(panes[0],'minsize'))
        maximum=self.winfo_width()-int(self.panecget(panes[1],'minsize'))-int(self.cget('sashwidth'))
        position=self.drag_origin[1]+event.x_root-self.drag_origin[0]
        self.sash_place(0,max(minimum,min(maximum,position)),0)

    def release(self,event):
        self.drag_origin=None;self.draw_divider();self.event_generate('<<SashMoved>>')


class SmoothScrollCanvas(tk.Canvas):
    """Pixel scrolling for settings and image grids, including small touchpad deltas."""
    def __init__(self,parent,**options):
        options.setdefault('yscrollincrement',1)
        super().__init__(parent,**options)
        self.scale=float(parent.tk.call('tk','scaling'))/(96/72)
        self._timer_owner=self.winfo_toplevel();self._scroll_timer=None;self._scroll_target=None
        self.bind('<MouseWheel>',self.wheel,add='+')
        self.bind('<Destroy>',self._dispose_scroll,add='+')

    def _scroll_metrics(self):
        values=self.tk.splitlist(self.cget('scrollregion'))
        total=max(1,float(values[3])-float(values[1])) if len(values)==4 else self.winfo_height()
        maximum=max(0,total-self.winfo_height())
        position=min(maximum,max(0,tk.Canvas.yview(self)[0]*total))
        return total,maximum,position

    def _cancel_scroll(self):
        if self._scroll_timer is not None:
            try:self._timer_owner.after_cancel(self._scroll_timer)
            except tk.TclError:pass
            self._scroll_timer=None

    def _dispose_scroll(self,event):
        if event.widget==self:self._cancel_scroll()

    def yview(self,*args):
        if args:self._cancel_scroll();self._scroll_target=None
        return tk.Canvas.yview(self,*args)

    def yview_moveto(self,fraction):self.yview('moveto',fraction)
    def yview_scroll(self,number,what):self.yview('scroll',number,what)

    def wheel(self,event):
        total,maximum,position=self._scroll_metrics()
        origin=position if self._scroll_target is None else self._scroll_target
        self._scroll_target=max(0,min(maximum,origin-float(event.delta)/120*44*self.scale))
        if self._scroll_timer is None:self._scroll_timer=self._timer_owner.after(12,self._animate_scroll)
        return 'break'

    def _animate_scroll(self):
        self._scroll_timer=None
        total,maximum,position=self._scroll_metrics()
        target=max(0,min(maximum,self._scroll_target if self._scroll_target is not None else position))
        distance=target-position
        if abs(distance)<=1:
            tk.Canvas.yview_moveto(self,target/total);self._scroll_target=None;return
        step=distance*.45
        if abs(step)<1:step=1 if step>0 else -1
        tk.Canvas.yview_moveto(self,(position+step)/total)
        self._scroll_timer=self._timer_owner.after(12,self._animate_scroll)
