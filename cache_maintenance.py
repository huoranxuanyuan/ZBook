# SPDX-License-Identifier: AGPL-3.0-only
"""Conservative cache retention with process leases and recoverable Windows cleanup."""
from contextlib import contextmanager
from pathlib import Path
import ctypes
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid

DAY=86400
COVER_DAYS=7
COVER_LIMIT=100_000_000
LOG_DAYS=30
LEGACY_GRACE=DAY
_LOCKS={}
_LOCKS_GUARD=threading.Lock()
_GUARD_STATE=threading.local()
LOG=logging.getLogger(__name__)


def native_recycle(paths):
    """IFileOperation requires recycling; there is deliberately no delete fallback."""
    if os.name!='nt':raise OSError('此平台未启用回收站清理，文件已保留')
    from ctypes import wintypes as w
    kernel=ctypes.WinDLL('kernel32')
    kernel.GetDriveTypeW.argtypes=[w.LPCWSTR];kernel.GetDriveTypeW.restype=w.UINT
    if any(kernel.GetDriveTypeW(str(Path(p).anchor))!=3 for p in paths):raise OSError('此位置不保证支持回收站，缓存已保留')
    class GUID(ctypes.Structure):
        _fields_=[('bytes',ctypes.c_ubyte*16)]
        def __init__(self,value):super().__init__((ctypes.c_ubyte*16).from_buffer_copy(uuid.UUID(value).bytes_le))
    ole=ctypes.OleDLL('ole32');shell=ctypes.WinDLL('shell32')
    ole.CoInitializeEx.argtypes=[ctypes.c_void_p,w.DWORD];ole.CoInitializeEx.restype=ctypes.c_long
    ole.CoCreateInstance.argtypes=[ctypes.POINTER(GUID),ctypes.c_void_p,w.DWORD,ctypes.POINTER(GUID),ctypes.POINTER(ctypes.c_void_p)]
    ole.CoCreateInstance.restype=ctypes.c_long
    shell.SHCreateItemFromParsingName.argtypes=[w.LPCWSTR,ctypes.c_void_p,ctypes.POINTER(GUID),ctypes.POINTER(ctypes.c_void_p)]
    shell.SHCreateItemFromParsingName.restype=ctypes.c_long
    def checked(hr):
        if hr<0:raise OSError(f'回收站操作失败：0x{hr & 0xffffffff:08X}')
    def call(obj,index,args=(),values=(),restype=ctypes.c_long):
        method=ctypes.cast(obj,ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents[index]
        return ctypes.WINFUNCTYPE(restype,ctypes.c_void_p,*args)(method)(obj,*values)
    initialized=ole.CoInitializeEx(None,2);checked(initialized)
    operation=ctypes.c_void_p();items=[]
    try:
        checked(ole.CoCreateInstance(ctypes.byref(GUID('3ad05575-8857-4850-9277-11b85bdb8e09')),None,1,ctypes.byref(GUID('947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8')),ctypes.byref(operation)))
        # FOFX_RECYCLEONDELETE, EARLYFAILURE, SILENT, NOCONFIRMATION,
        # NOERRORUI, NO_CONNECTED_ELEMENTS, ALLOWUNDO.
        checked(call(operation,5,(w.DWORD,),(0x00080000|0x00100000|0x0004|0x0010|0x0400|0x2000|0x0040,)))
        for path in paths:
            item=ctypes.c_void_p()
            checked(shell.SHCreateItemFromParsingName(str(path),None,ctypes.byref(GUID('43826d1e-e718-42ee-bc55-a1e261c37bfe')),ctypes.byref(item)))
            items.append(item)
            checked(call(operation,18,(ctypes.c_void_p,ctypes.c_void_p),(item,None)))
        checked(call(operation,21))
        aborted=w.BOOL();checked(call(operation,22,(ctypes.POINTER(w.BOOL),),(ctypes.byref(aborted),)))
        if aborted:raise OSError('部分文件未能移入回收站，已保留以待下次重试')
    finally:
        for item in items:call(item,2,restype=w.ULONG)
        if operation:call(operation,2,restype=w.ULONG)
        ole.CoUninitialize()


def _lock(stream,wait=False):
    stream.seek(0)
    if os.name=='nt':
        import msvcrt
        msvcrt.locking(stream.fileno(),msvcrt.LK_LOCK if wait else msvcrt.LK_NBLCK,1)
    else:
        import fcntl
        fcntl.flock(stream,fcntl.LOCK_EX|(0 if wait else fcntl.LOCK_NB))


def _unlock(stream):
    stream.seek(0)
    if os.name=='nt':
        import msvcrt
        msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
    else:
        import fcntl
        fcntl.flock(stream,fcntl.LOCK_UN)


def touch_covers(rows,folder):
    folder=Path(folder).resolve()
    for row in rows:
        path=Path(row.path)
        try:
            if path.resolve().parent==folder and re.fullmatch(r'[0-9a-f]{64}\.(jpg|png|webp)',path.name):os.utime(path,None)
        except OSError:pass


class CacheMaintenance:
    def __init__(self,root,cover_dir=None,kind='gui',recycle=None,clock=None):
        self.root=Path(root).resolve();self.cover_dir=Path(cover_dir or self.root/'data'/'covers').resolve()
        self.folder=self.root/'archive'/'maintenance';self.leases=self.folder/'leases'
        self.leases.mkdir(parents=True,exist_ok=True)
        self.token=uuid.uuid4().hex;self.resources=[];self.protected=[];self.kind=kind
        self.clock=clock or time.time;self.recycle=recycle or native_recycle;self.closed=False;self.worker=None
        self.lease_path=self.leases/(self.token+'.lock');self.meta_path=self.leases/(self.token+'.json')
        with self.guard():
            self.handle=self.lease_path.open('w+b');self.handle.write(b'0');self.handle.flush();_lock(self.handle)
            self._save_meta()

    @contextmanager
    def guard(self):
        key=str(self.root)
        with _LOCKS_GUARD:mutex=_LOCKS.setdefault(key,threading.RLock())
        with mutex:
            active=getattr(_GUARD_STATE,'roots',set())
            if key in active:
                yield
                return
            path=self.folder/'guard.lock'
            with path.open('a+b') as stream:
                if stream.tell()==0:stream.write(b'0');stream.flush()
                deadline=time.monotonic()+20
                while True:
                    try:_lock(stream);break
                    except OSError:
                        if time.monotonic()>=deadline:raise TimeoutError('另一清理任务正在运行，本次保留缓存')
                        time.sleep(.02)
                _GUARD_STATE.roots=active|{key}
                try:yield
                finally:_GUARD_STATE.roots=active;_unlock(stream)

    def _save_meta(self):
        self.meta_path.write_text(json.dumps({'pid':os.getpid(),'parent_pid':os.getppid(),'kind':self.kind,'resources':[str(p) for p in self.resources]},ensure_ascii=False),encoding='utf-8')

    def protect_resource(self,path):
        path=Path(path).resolve()
        if not path.is_relative_to(self.root/'archive'):raise ValueError('缓存资源不在软件工作目录内')
        with self.guard():
            self.resources.append(path);self._save_meta()

    def _live(self):
        live=[];stale=[];expired=[]
        for path in self.leases.glob('*.lock'):
            metadata=path.with_suffix('.json')
            try:data=json.loads(metadata.read_text(encoding='utf-8'))
            except (OSError,ValueError):data={}
            if path==self.lease_path and not self.closed:live.append((path.stem,data));continue
            try:
                with path.open('r+b') as stream:_lock(stream);_unlock(stream)
            except OSError:live.append((path.stem,data));continue
            stale.extend([path,metadata]);expired.extend(Path(p) for p in data.get('resources',[]))
        return live,stale,expired

    def safe(self,path):
        path=Path(path).absolute()
        try:
            if not path.exists() or path==self.root or not path.resolve().is_relative_to(self.root):return False
            for part in [path]+list(path.parents):
                if part==self.root:break
                if part.is_symlink() or getattr(part.lstat(),'st_file_attributes',0)&0x400:return False
            return path.is_relative_to(self.root/'archive') or path.parent==self.cover_dir or path.is_relative_to(self.root/'data'/'browser-search')
        except OSError:return False

    def _saved_outputs(self):
        try:return set(json.loads((self.folder/'protected-outputs.json').read_text(encoding='utf-8')))
        except (OSError,ValueError):return set()

    def remember_outputs(self,outputs):
        values=self._saved_outputs()
        for output in outputs:
            path=Path(output).resolve()
            if path.is_relative_to(self.root/'archive') or path.is_relative_to(self.cover_dir):values.add(str(path))
        if values:(self.folder/'protected-outputs.json').write_text(json.dumps(sorted(values),ensure_ascii=False),encoding='utf-8')

    def _record(self,path,size,reason,error=''):
        line={'time':self.clock(),'path':str(path),'bytes':size,'reason':reason,'result':'retained' if error else 'recycled','error':error}
        try:
            with (self.folder/('cleanup-'+time.strftime('%Y%m%d',time.localtime(self.clock()))+'.jsonl')).open('a',encoding='utf-8') as stream:stream.write(json.dumps(line,ensure_ascii=False)+'\n')
        except OSError:LOG.exception('无法保存缓存清理记录')

    def _recycle(self,paths,reason,protected=()):
        protected=[Path(p).resolve() for p in list(protected)+list(self._saved_outputs())+self.protected]
        candidates=[];sizes={}
        for path in dict.fromkeys(Path(p) for p in paths):
            if not self.safe(path):continue
            resolved=path.resolve()
            if any(resolved==p or p.is_relative_to(resolved) or resolved.is_relative_to(p) for p in protected):continue
            candidates.append(path)
            try:sizes[path]=sum(p.stat().st_size for p in path.rglob('*') if p.is_file()) if path.is_dir() else path.stat().st_size
            except OSError:sizes[path]=0
        if not candidates:return 0
        error=''
        try:self.recycle(candidates)
        except Exception as exc:error=str(exc);LOG.info('缓存清理稍后重试：%s',error)
        count=0
        for path in candidates:
            remaining=path.exists();count+=not remaining
            self._record(path,sizes[path],reason,(error or '文件仍被占用') if remaining else '')
        return count

    def cleanup_work(self,work,outputs=(),reason='转换中间文件'):
        try:
            with self.guard():
                self.remember_outputs(outputs)
                work=Path(work)
                files=[p for p in work.iterdir() if p.is_file() and (p.name in ('book.epub','book.mobi','encoder.epub') or re.fullmatch(r'\.ZBook写入中-[0-9a-f]{32}\.tmp',p.name))]
                return self._recycle(files,reason,outputs)
        except Exception:LOG.exception('转换缓存暂未清理，导出结果不受影响');return 0

    def _legacy_active(self,live):
        if os.name!='nt':return False
        # Older releases have no leases. Their visible main window must also be respected.
        from ctypes import wintypes as w
        pids={os.getpid()}
        for _,data in live:pids.update([data.get('pid'),data.get('parent_pid')])
        found=[];user=ctypes.windll.user32
        callback_type=ctypes.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
        def visit(hwnd,_):
            if not user.IsWindowVisible(hwnd):return True
            title=ctypes.create_unicode_buffer(256);user.GetWindowTextW(hwnd,title,256)
            if title.value.startswith('ZBook'):
                pid=w.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
                if pid.value not in pids:found.append(pid.value)
            return True
        callback=callback_type(visit);user.EnumWindows(callback,0)
        return bool(found)

    def _sweep_locked(self):
        now=self.clock();live,stale,expired=self._live()
        resources=[Path(p).resolve() for _,data in live for p in data.get('resources',[])]
        other=any(token!=self.token for token,_ in live) or self._legacy_active(live)
        preview=self.root/'archive'/'preview-cache'
        if preview.is_dir():
            for folder in preview.iterdir():
                if not folder.is_dir() or not re.fullmatch(r'[0-9a-f]{32}',folder.name):continue
                if any(folder.resolve()==p or folder.resolve().is_relative_to(p) for p in resources):continue
                if other and folder not in expired and now-folder.stat().st_mtime<LEGACY_GRACE:continue
                files=list(folder.iterdir())
                if all(p.is_file() and re.fullmatch(r'[a-zA-Z0-9]+-\d+\.(json\.gz|json\.pending)',p.name) for p in files):self._recycle([folder],'退出或异常退出的预览缓存',resources)
        runs=self.root/'archive'/'runs'
        if runs.is_dir():
            for run in runs.iterdir():
                if not run.is_dir() or not re.fullmatch(r'\d{8}-\d{6}-[0-9a-f]{6}',run.name) or not self.safe(run):continue
                if any(run.resolve()==p or run.resolve().is_relative_to(p) for p in resources):continue
                report=run/'report.json'
                if other and not report.exists() and run not in expired and now-run.stat().st_mtime<LEGACY_GRACE:continue
                outputs=[];valid=True
                try:
                    if report.exists():outputs=[p for row in json.loads(report.read_text(encoding='utf-8')).get('results',[]) for p in row.get('outputs',[])]
                    for job in run.iterdir():
                        if not job.is_dir() or not re.fullmatch(r'[0-9a-f]{32}',job.name) or not self.safe(job):continue
                        result=job/'result.json'
                        if result.exists():outputs.extend(json.loads(result.read_text(encoding='utf-8')).get('outputs',[]))
                except (OSError,ValueError):valid=False
                if not valid:continue
                self.remember_outputs(outputs)
                for job in run.iterdir():
                    if not job.is_dir() or not re.fullmatch(r'[0-9a-f]{32}',job.name) or not self.safe(job):continue
                    result=job/'result.json'
                    try:row=json.loads(result.read_text(encoding='utf-8')) if result.exists() else {}
                    except (OSError,ValueError):continue
                    if row.get('status')=='完成':
                        if not row.get('published_verified'):
                            try:verify_published_files(job,row.get('outputs',[]))
                            except OSError:continue
                    elif row and now-result.stat().st_mtime<LOG_DAYS*DAY:continue
                    self.cleanup_work(job,outputs,'已结束或异常退出的转换缓存')
        if not other:
            self._sweep_covers(now,resources)
            self._sweep_browser(now,resources)
            self._sweep_logs(now,resources)
        empty=[]
        for name in ('runs','cover-search','outline'):
            base=self.root/'archive'/name
            if not base.is_dir():continue
            for folder in sorted(base.rglob('*'),key=lambda p:len(p.parts),reverse=True):
                if folder.is_dir() and re.fullmatch(r'(?:[0-9a-f]{32}|\d{8}-\d{6}-[0-9a-f]{6}|\d{15,20})',folder.name) and self.safe(folder) and not any(folder.iterdir()):empty.append(folder)
        self._recycle(empty,'已清空的任务缓存目录',resources)
        self._recycle([p for p in stale if p.exists()],'已结束任务的缓存锁',resources)

    def _sweep_covers(self,now,resources):
        if not self.cover_dir.is_dir() or not self.cover_dir.is_relative_to(self.root):return
        files=[p for p in self.cover_dir.iterdir() if p.is_file() and re.fullmatch(r'[0-9a-f]{64}\.(jpg|png|webp|json)',p.name) and self.safe(p)]
        protected={Path(p).resolve() for p in self.protected+self._saved_outputs_as_paths()}
        candidates=[p for p in files if p.resolve() not in protected]
        expired=[p for p in candidates if now-self._cover_last_used(p)>COVER_DAYS*DAY]
        self._recycle(expired,'超过7天未使用的封面缓存',resources)
        self._prune_cover_manifests(resources)
        remaining=[p for p in files if p.exists()];size=sum(p.stat().st_size for p in remaining)
        remove=[]
        for p in sorted(remaining,key=self._cover_last_used):
            if size<=COVER_LIMIT:break
            if p.resolve() in protected:continue
            remove.append(p);size-=p.stat().st_size
        self._recycle(remove,'封面缓存超过100MB，淘汰最久未用文件',resources)
        self._prune_cover_manifests(resources)

    def _sweep_browser(self,now,resources):
        # Old data/browser-search is included for upgrades. Live leases and the
        # shared render lock protect all profiles, even if lease setup failed.
        for root in (self.root/'archive'/'browser-search',self.root/'data'/'browser-search'):
            if not root.is_dir() or not self.safe(root):continue
            if any(root.resolve()==p or p.is_relative_to(root.resolve()) for p in resources):continue
            try:
                with (root/'.render.lock').open('a+b') as guard:
                    if guard.tell()==0:guard.write(b'0');guard.flush()
                    try:_lock(guard)
                    except OSError:continue
                    try:
                        files=[p for p in root.iterdir() if p.is_file() and re.fullmatch(r'[0-9a-f]{64}\.(html|log)',p.name) and self.safe(p)]
                        remaining=sum(p.stat().st_size for p in files)
                        remove=[]
                        for path in sorted(files,key=lambda p:p.stat().st_mtime):
                            if now-path.stat().st_mtime>7*DAY or remaining>32_000_000:
                                remove.append(path);remaining-=path.stat().st_size
                        self._recycle(remove,'浏览器检索缓存保留7天、最多32MB',resources)
                        profile=root/'profile'
                        if profile.is_dir() and self.safe(profile):
                            # Never follow junctions when measuring browser data.
                            size=sum(p.stat().st_size for p in profile.rglob('*') if p.is_file() and self.safe(p))
                            if now-profile.stat().st_mtime>7*DAY or size>128_000_000:
                                self._recycle([profile],'闲置7天或超过128MB的独立搜索浏览器配置',resources)
                    finally:_unlock(guard)
            except OSError:LOG.info('浏览器缓存正在使用，本次保留')

    def _cover_last_used(self,path):
        used=path.stat().st_mtime
        if path.suffix=='.json':
            try:
                for row in json.loads(path.read_text(encoding='utf-8')):
                    image=Path(row['path'])
                    if image.resolve().parent==self.cover_dir and self.safe(image):used=max(used,image.stat().st_mtime)
            except (OSError,ValueError,KeyError,TypeError):pass
        return used

    def _prune_cover_manifests(self,resources):
        # Stale manifests are never used after one of their referenced images was evicted.
        invalid=[]
        for path in self.cover_dir.glob('*.json'):
            if not re.fullmatch(r'[0-9a-f]{64}\.json',path.name):continue
            try:
                rows=json.loads(path.read_text(encoding='utf-8'))
                if isinstance(rows,list) and any(not Path(row['path']).is_file() for row in rows):invalid.append(path)
            except (OSError,ValueError,KeyError,TypeError):continue
        self._recycle(invalid,'封面图片已清理，对应检索索引失效',resources)

    def _saved_outputs_as_paths(self):return [Path(p) for p in self._saved_outputs()]

    def _sweep_logs(self,now,resources):
        cutoff=now-LOG_DAYS*DAY;candidates=[]
        roots=[self.root/'archive'/name for name in ('runs','cover-search','outline','logs','maintenance')]
        allowed={'progress.log','encoder.log','error.log','result.json','report.json','application.log'}
        for root in roots:
            if not root.is_dir():continue
            for p in root.rglob('*'):
                if not p.is_file() or not self.safe(p):continue
                name=p.name
                if name not in allowed and not re.fullmatch(r'(?:application-\d{8}\.log|conversion-error-\d+\.log|cleanup-\d{8}\.jsonl)',name):continue
                if p.stat().st_mtime<cutoff:candidates.append(p)
        self._recycle(candidates,'保留30天后的任务与错误日志',resources)

    def sweep(self):
        try:
            with self.guard():self._sweep_locked()
        except Exception:LOG.exception('自动清理暂未完成，缓存已保留')

    def start_sweep(self,protected=()):
        self.protected=[Path(p).resolve() for p in protected if p]
        if self.closed or self.worker is not None and self.worker.is_alive():return
        self.worker=threading.Thread(target=self.sweep,name='zbook-cache-maintenance',daemon=True);self.worker.start()

    def finish(self):
        if self.closed:return
        try:
            if self.worker is not None:self.worker.join()
            with self.guard():
                for resource in self.resources:
                    if resource.parent==self.root/'archive'/'preview-cache' and resource.exists():
                        self._recycle([p for p in resource.iterdir() if p.is_file() and re.fullmatch(r'[a-zA-Z0-9]+-\d+\.(json\.gz|json\.pending)',p.name)],'正常退出的章节预览缓存')
                        if not list(resource.iterdir()):self._recycle([resource],'空预览缓存目录')
                _unlock(self.handle);self.handle.close();self.closed=True
                self._sweep_locked()
        except Exception:
            LOG.exception('退出清理未完成，将在下次启动补清')
            if not self.handle.closed:self.handle.close()
            self.closed=True

    def __del__(self):
        handle=getattr(self,'handle',None)
        if handle is not None and not handle.closed:handle.close()


def verify_published_files(work,outputs):
    for output in outputs:
        target=Path(output);source=Path(work)/('book'+target.suffix.lower())
        if not target.is_file() or source.stat().st_size!=target.stat().st_size:raise OSError('导出文件校验失败，中间文件已保留')
        with source.open('rb') as a,target.open('rb') as b:
            if hashlib.file_digest(a,'sha256').digest()!=hashlib.file_digest(b,'sha256').digest():raise OSError('导出文件内容校验失败，中间文件已保留')


class _NoMaintenance:
    """Unavailable cleanup must never prevent importing, searching, or exporting."""
    def protect_resource(self,*args,**kwargs):pass
    def cleanup_work(self,*args,**kwargs):return 0
    def start_sweep(self,*args,**kwargs):pass
    def finish(self):pass


def create_maintenance(root,cover_dir=None,kind='gui'):
    try:return CacheMaintenance(root,cover_dir,kind)
    except Exception:
        LOG.exception('本次未能初始化自动清理，保留缓存并继续正常使用')
        return _NoMaintenance()
