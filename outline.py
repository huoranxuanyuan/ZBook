# SPDX-License-Identifier: AGPL-3.0-only
"""Editable full-book / volume / chapter navigation for the desktop workspace."""
import tkinter as tk
from tkinter import messagebox,simpledialog
from core import count_chapters




def _editable(ui):
    job=ui.current_job()
    return job if job and ui.parsed and not ui.busy and job.id not in ui.preparing else None


def render_outline(ui,book):
    ui.parsed=book;job=ui.current_job()
    if not job:return
    tree=ui.chapter_tree;ui.outline_nodes={};tree.delete(*tree.get_children())
    chapters=book.chapters;volumes=[index for index,chapter in enumerate(chapters) if chapter.kind=='volume']
    count=count_chapters(book)
    ui.preview_info.set(f'{count} 章'+(f' · {len(volumes)} 卷' if volumes else ' · 无分卷')+f' · {book.encoding} · {book.characters:,} 字符')
    ui.notes.set('\n'.join(dict.fromkeys(job.notes+book.warnings)) or '目录已整理。点击卷名展开章节，点击章节预览正文。')
    tree.insert('','end',iid='full',text='全文',values=('—',),open=True,tags=('full',))
    ui.outline_nodes['full']={'kind':'all','start':0,'end':len(chapters)}
    parent=None
    for index,chapter in enumerate(chapters):
        item=str(index)
        if chapter.kind=='volume':
            stop=next((other for other in volumes if other>index),len(chapters))
            tree.insert('full','end',iid=item,text=chapter.title,values=(chapter.line if chapter.line>0 else '—',),open=False,tags=('volume',))
            ui.outline_nodes[item]={'kind':'volume','start':index,'end':stop};parent=item
        else:
            destination=parent or 'full'
            tree.insert(destination,'end',iid=item,text=chapter.title,values=(chapter.line,),tags=('chapter',))
            ui.outline_nodes[item]={'kind':chapter.kind,'start':index,'end':index+1}
    previous=ui.preview_lines.get(job.id)
    if previous==0:item='full'
    else:
        index=next((index for index,chapter in enumerate(chapters) if chapter.line==previous),None)
        if index is None and previous is not None:index=next((index for index in range(len(chapters)-1,-1,-1) if 0<chapters[index].line<previous and chapters[index].kind=='chapter'),None)
        if index is None:index=next((index for index,chapter in enumerate(chapters) if chapter.kind=='chapter'),0)
        item=str(index) if chapters else 'full'
    # Selecting a hidden chapter would auto-open its volume through see().
    if volumes and item!='full':
        parent=tree.parent(item)
        if parent and parent!='full':item=parent
        elif previous is None:item=str(volumes[0])
    tree.selection_set(item);tree.focus(item);tree.see(item);show_selection(ui);ui.update_start()


def show_selection(ui):
    if not ui.parsed:return
    selected=ui.chapter_tree.selection()
    if not selected:return
    nodes=getattr(ui,'outline_nodes',{})
    ranges=[nodes[item] for item in selected if item in nodes]
    if not ranges:return
    indices=sorted({index for node in ranges for index in range(node['start'],node['end'])})
    if not indices:return
    first=ui.parsed.chapters[indices[0]];job=ui.current_job()
    if job:ui.preview_lines[job.id]=0 if selected==('full',) else first.line
    label='全文 · '+job.title if selected==('full',) and job else ui.chapter_tree.item(selected[0],'text')
    if len(selected)>1:label=f'已选 {len(selected)} 个章节'
    ui.preview_title.set(label)
    parts=[];remaining=24000;truncated=False
    for index in indices:
        chapter=ui.parsed.chapters[index]
        text=(chapter.title+'\n\n' if len(indices)>1 else '')+chapter.text
        parts.append(text[:remaining]);remaining-=min(len(text),remaining)
        if remaining<=0:truncated=True;break
    text='\n\n'.join(parts)
    if truncated:text+='\n\n（预览截取前 24000 字；导出保留全文）'
    ui.set_text(ui.body,text);ui.update_start()


def _refresh(ui,job,message,line=None):
    if line is not None:ui.preview_lines[job.id]=line
    job.status='正在更新目录';ui.outline_messages[job.id]=message
    ui.preview_cover()
    ui.log(message);ui.prepare_job(job)


def rename_selection(ui):
    job=_editable(ui);selected=ui.chapter_tree.selection()
    if not job or not selected:return
    node=ui.outline_nodes.get(selected[0])
    if not node or node['kind']!='chapter':return
    chapter=ui.parsed.chapters[node['start']]
    name=simpledialog.askstring('修改章节名称','新的章节名（原文件保持不变）：',initialvalue=chapter.title,parent=ui.root)
    if name and name.strip():
        job.chapter_titles[str(chapter.line)]=name.strip()
        _refresh(ui,job,'已修改章节名称：'+name.strip(),chapter.line)








def menu(ui,event):
    item=ui.chapter_tree.identify_row(event.y)
    if item:ui.chapter_tree.selection_set(item)
    node=ui.outline_nodes.get(item,{})
    context=tk.Menu(ui.root,tearoff=False)
    context.add_command(label='章节改名',command=lambda:rename_selection(ui),state='normal' if _editable(ui) and node.get('kind')=='chapter' else 'disabled')
    context.add_command(label='重新识别目录',command=ui.reset_chapters,state='normal' if _editable(ui) else 'disabled')
    context.tk_popup(event.x_root,event.y_root)
