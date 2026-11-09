# SPDX-License-Identifier: AGPL-3.0-only
"""Serial cover preparation; workers report values and never touch UI widgets."""
from pathlib import Path
import threading
from covers import search_details, first_matching_cover


def prepare_covers(jobs, emit=None, cancel=None):
    emit=emit or (lambda *args:None)
    cancel=cancel or threading.Event()
    results=[]
    for index,job in enumerate(jobs,1):
        if cancel.is_set():break
        emit('prepare_current',job.id,index,len(jobs))
        emit('prepare_progress',index-1,len(jobs),f'联网刮削封面 {index}/{len(jobs)} · {job.title}')
        row=None;error='';kept=False;reason='';diagnostics=[]
        try:
            if job.cover_path and Path(job.cover_path).is_file():kept=True
            else:
                found=search_details(job.title,job.author,progress=lambda text:emit('prepare_progress',index-1,len(jobs),f'{index}/{len(jobs)} · {job.title} · {text}'))
                if cancel.is_set():break
                diagnostics=list(found.diagnostics)
                row=first_matching_cover(found.covers,job.title,job.author)
                if not row:reason='未找到可用的书名/作者匹配封面，暂用文字封面'
                if row:emit('prepare_progress',index-1,len(jobs),f'正在应用图片封面 {index}/{len(jobs)} · {job.title}')
        except Exception as exc:error=str(exc);reason='搜索失败：'+error
        result={'id':job.id,'title':job.title,'cover':row,'kept':kept,'error':error,'reason':reason,'diagnostics':diagnostics}
        results.append(result)
        emit('prepared_cover',result)
        emit('prepare_progress',index,len(jobs),f'已处理封面 {index}/{len(jobs)}')
    return {'requested':len(jobs),'results':results,'stopped':cancel.is_set()}
