# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded document parsing with a killable worker and a fixed-size JSON result."""

from contextlib import contextmanager
from contextvars import ContextVar
import json
from multiprocessing.shared_memory import SharedMemory
import os
from pathlib import Path
import subprocess
import sys
import time

MEMORY_BYTES = 512 * 1024 * 1024
MAX_CHARS = 4_000_000
MAX_PAGES = 4000
MAX_LINES = 160_000
RESULT_BYTES = 24 * 1024 * 1024
CANCELLED = ContextVar("document_cancelled", default=lambda: False)


@contextmanager
def document_cancellation(check):
    token = CANCELLED.set(check)
    try:
        yield
    finally:
        CANCELLED.reset(token)


def limit_memory():
    if os.name != "nt":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
        return
    import ctypes as c
    from ctypes import wintypes as w

    class Basic(c.Structure):
        _fields_ = [
            ("process_time", c.c_int64),
            ("job_time", c.c_int64),
            ("flags", w.DWORD),
            ("min_ws", c.c_size_t),
            ("max_ws", c.c_size_t),
            ("active", w.DWORD),
            ("affinity", c.c_size_t),
            ("priority", w.DWORD),
            ("scheduling", w.DWORD),
        ]

    class Extended(c.Structure):
        _fields_ = [
            ("basic", Basic),
            ("io", c.c_uint64 * 6),
            ("process_memory", c.c_size_t),
            ("job_memory", c.c_size_t),
            ("peak_process", c.c_size_t),
            ("peak_job", c.c_size_t),
        ]

    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [w.LPVOID, w.LPCWSTR]
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, w.LPVOID, w.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    kernel.GetCurrentProcess.restype = w.HANDLE
    job = kernel.CreateJobObjectW(None, None)
    limits = Extended()
    limits.basic.flags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
    limits.process_memory = MEMORY_BYTES
    if not (
        job
        and kernel.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits))
        and kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess())
    ):
        raise OSError("无法设置文档解析进程的内存上限")
    # The OS releases this job handle when this short-lived process exits.


def worker_main(args):
    from dataclasses import asdict
    from input_sources import _read_pdf, _read_rtf

    name, kind, filename, length = args
    block = SharedMemory(name=name, track=False)
    try:
        try:
            limit_memory()
            reader = {"pdf": _read_pdf, "rtf": _read_rtf}[kind]
            doc = reader(Path(filename), int(length) or None)
            payload = json.dumps(asdict(doc), ensure_ascii=False).encode("utf-8")
            if len(payload) > block.size - 4:
                raise ValueError("文档展开结果过大，请拆分后导入")
        except (ValueError, OSError, MemoryError) as exc:
            message = str(exc)[:320] or "文档解析内存不足，请拆分后导入"
            payload = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
        block.buf[4 : 4 + len(payload)] = payload
        block.buf[:4] = len(payload).to_bytes(4, "little")
    finally:
        block.close()


def read_isolated(path, kind, limit):
    from input_sources import DocumentText

    cancelled = CANCELLED.get()
    if cancelled():
        raise InterruptedError("已停止文档解析")
    deadline = time.monotonic() + (25 if limit else 90)
    # Header previews need little space, even for a large original document.
    block = SharedMemory(
        create=True, size=RESULT_BYTES if not limit else min(RESULT_BYTES, 65536 + 16 * limit)
    )
    try:
        entry = [] if getattr(sys, "frozen", False) else ["-B", str(Path(__file__).resolve())]
        command = [
            sys.executable,
            *entry,
            "--document-worker",
            block.name,
            kind,
            str(path.resolve()),
            str(limit or 0),
        ]
        with subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ) as child:
            try:
                while child.poll() is None:
                    if cancelled():
                        raise InterruptedError("已停止文档解析")
                    if time.monotonic() >= deadline:
                        raise ValueError("文档解析超时，请拆分后重新导入")
                    time.sleep(0.05)
            finally:
                if child.poll() is None:
                    child.kill()
            length = int.from_bytes(block.buf[:4], "little")
            if child.returncode or not 0 < length <= block.size - 4:
                raise ValueError("文档解析进程未完成，可能超出资源上限或文件损坏。请拆分后重试。")
            result = json.loads(bytes(block.buf[4 : 4 + length]))
        if "error" in result:
            raise ValueError(result["error"])
        headings = {int(k): v for k, v in result["headings"].items()}
        return DocumentText(
            result["text"], result["format"], headings, result["warnings"], result["title_hint"]
        )
    finally:
        block.close()
        block.unlink()


if __name__ == "__main__" and sys.argv[1:2] == ["--document-worker"]:
    worker_main(sys.argv[2:])
