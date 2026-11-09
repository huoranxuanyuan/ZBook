# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded source discovery and shared desktop/encoder limits."""
from dataclasses import dataclass, field
from pathlib import Path
import os
from input_sources import SUPPORTED_SUFFIXES

MAX_BATCH_FILES = 50
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_BATCH_BYTES = 512 * 1024 * 1024
LIMIT_DESCRIPTION = '每批最多 50 本 · 单文件 64 MB · 合计 512 MB'


@dataclass
class SourceBatch:
    paths: list[Path] = field(default_factory=list)
    truncated: bool = False
    byte_limited: bool = False
    oversized: int = 0
    duplicates: int = 0
    scanned: int = 0
    errors: list[str] = field(default_factory=list)
    stopped: bool = False
    attempted: list[Path] = field(default_factory=list)
    rejected: bool = False


def _sources(values, cancel, errors):
    def walk(path, depth=0):
        if cancel is not None and cancel.is_set():return
        try:
            if path.is_dir():
                if depth>48:
                    errors.append('文件夹层级过深：'+str(path));return
                with os.scandir(path) as entries:
                    for entry in entries:
                        if cancel is not None and cancel.is_set():return
                        if entry.is_dir(follow_symlinks=False):yield from walk(Path(entry.path),depth+1)
                        elif entry.is_file():yield from walk(Path(entry.path),depth+1)
            elif path.suffix.lower() in SUPPORTED_SUFFIXES and not path.name.startswith('~$') and path.is_file():
                yield path.resolve()
        except OSError as exc:
            if len(errors)<8:errors.append(str(path)+': '+str(exc))
    for value in values:
        yield from walk(Path(value))


def collect_sources(values, known=(), cancel=None, excluded=()):
    result=SourceBatch()
    known_paths={os.path.normcase(str(Path(path).resolve())) for path in known}
    remaining=max(0,MAX_BATCH_FILES-len(known_paths))
    if not remaining:result.truncated=True;result.rejected=True;return result
    total=0
    for path in known_paths:
        try:total+=Path(path).stat().st_size
        except OSError:pass
    seen=set(known_paths)|{os.path.normcase(str(Path(path).resolve())) for path in excluded}
    unique_pending=0
    candidates=_sources(values,cancel,result.errors)
    try:
        for path in candidates:
            result.scanned+=1
            if cancel is not None and cancel.is_set():break
            key=os.path.normcase(str(path))
            if key in seen:result.duplicates+=1;continue
            seen.add(key)
            unique_pending+=1
            if unique_pending>remaining:
                result.truncated=True;result.rejected=True;result.paths.clear();break
            try:size=path.stat().st_size
            except OSError as exc:
                if len(result.errors)<8:result.errors.append(path.name+': '+str(exc))
                continue
            if size>MAX_FILE_BYTES:result.oversized+=1;continue
            if total+size>MAX_BATCH_BYTES:result.byte_limited=True;continue
            result.paths.append(path);total+=size
    finally:candidates.close()
    result.stopped=bool(cancel is not None and cancel.is_set())
    return result


def validate_batch(jobs):
    if len(jobs)>MAX_BATCH_FILES:raise ValueError(f'每批最多转换 {MAX_BATCH_FILES} 本，请拆分批次')
    total=0
    for job in jobs:
        path=Path(job.path)
        try:size=path.stat().st_size
        except OSError:continue
        if size>MAX_FILE_BYTES:raise ValueError(f'{path.name} 超过单文件 64 MB 上限，请拆分后导入')
        total+=size
    if total>MAX_BATCH_BYTES:raise ValueError('本批源文件合计超过 512 MB，请减少书籍数量')


def import_summary(count, batch):
    if batch.rejected:return '无法导入：列表最多 50 本，本次未导入；请减少文件或拆分文件夹'
    text=f'已导入 {count} 本'
    if batch.truncated:text+=' · 达到每批 50 本上限，其余文件未导入'
    if batch.byte_limited:text+=' · 已跳过超出合计 512 MB 的文件'
    if batch.oversized:text+=f' · {batch.oversized} 个文件超过 64 MB'
    if batch.duplicates:text+=f' · 跳过 {batch.duplicates} 个重复文件'
    if batch.stopped:text+=' · 已停止'
    return text
