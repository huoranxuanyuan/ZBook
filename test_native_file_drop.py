# SPDX-License-Identifier: AGPL-3.0-only
"""Exercise real WM_DROPFILES in guarded child processes, not direct callbacks."""

from pathlib import Path
import ctypes as C
from ctypes import wintypes as W
import json
import os
import subprocess
import sys
import tempfile
import unittest


def native_probe(folder, scenario):
    import faulthandler
    import shutil
    import time
    import tkinter as tk

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    os.environ["ZBOOK_DATA_DIR"] = str(folder / "runtime")
    sys.dont_write_bytecode = True
    faulthandler.enable()
    faulthandler.dump_traceback_later(15, exit=True)
    import socket

    def block_network(*args, **kwargs):
        raise OSError("Network disabled in native drop probe")

    socket.socket.connect = block_network
    socket.getaddrinfo = block_network
    import app
    import cache_maintenance

    trash = folder / "retained-test-trash"

    def retain(paths):
        import uuid

        trash.mkdir(exist_ok=True)
        for path in paths:
            if Path(path).exists():
                shutil.move(str(path), str(trash / (uuid.uuid4().hex + "-" + Path(path).name)))

    cache_maintenance.native_recycle = retain
    warnings = []
    app.messagebox.showwarning = lambda *args, **kwargs: warnings.append(str(args))
    root = tk.Tk()
    root.withdraw()
    widget = app.ZBook(root)
    drop = widget.file_drop
    events = []
    ticks = []
    in_native = [False]
    tk_reentries = []
    native_count = []
    original_after = root.after

    def checked_after(*args, **kwargs):
        if in_native[0]:
            tk_reentries.append(True)
        return original_after(*args, **kwargs)

    root.after = checked_after
    receive = drop._receive

    def checked_receive(handle):
        in_native[0] = True
        native_count.append(True)
        try:
            return receive(handle)
        finally:
            in_native[0] = False

    drop._receive = checked_receive
    original_files = drop.on_files

    def on_files(paths):
        events.append(list(paths))
        if scenario == "callback-error" and len(events) == 1:
            raise RuntimeError("controlled callback failure")
        original_files(paths)

    drop.on_files = on_files
    limits = []
    drop.on_limit = lambda: limits.append(True)
    sources = []
    source_dir = folder / "书籍 文件夹"
    source_dir.mkdir()
    for index in range(51 if scenario == "large-folder" else 3):
        source = source_dir / f"《拖放小说 {index}》作者：测试作者{index}.txt"
        source.write_text(
            "第一卷 测试\n第一章 开始\n正文。\n第二章 结束\n完整末段。", encoding="utf-8"
        )
        sources.append(str(source))
    paths = [str(source_dir)] if scenario in ("folder", "large-folder") else sources
    if scenario == "limit":
        paths = [str(folder / f"多选{index}.txt") for index in range(51)]
    kernel = C.WinDLL("kernel32", use_last_error=True)
    user = C.WinDLL("user32", use_last_error=True)
    kernel.GlobalAlloc.argtypes = [W.UINT, C.c_size_t]
    kernel.GlobalAlloc.restype = W.HGLOBAL
    kernel.GlobalLock.argtypes = [W.HGLOBAL]
    kernel.GlobalLock.restype = C.c_void_p
    kernel.GlobalUnlock.argtypes = [W.HGLOBAL]
    user.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    user.GetParent.argtypes = [W.HWND]
    user.GetParent.restype = W.HWND
    get_style = user.GetWindowLongPtrW if C.sizeof(C.c_void_p) == 8 else user.GetWindowLongW
    get_style.argtypes = [W.HWND, C.c_int]
    get_style.restype = C.c_ssize_t

    class DROPFILES(C.Structure):
        _fields_ = [("pFiles", W.DWORD), ("pt", W.POINT), ("fNC", W.BOOL), ("fWide", W.BOOL)]

    def post():
        block = ("\0".join(paths) + "\0\0").encode("utf-16-le")
        header = DROPFILES(C.sizeof(DROPFILES), W.POINT(5, 5), False, True)
        handle = kernel.GlobalAlloc(0x42, C.sizeof(header) + len(block))
        pointer = kernel.GlobalLock(handle)
        if not pointer:
            raise C.WinError(C.get_last_error())
        C.memmove(pointer, C.byref(header), C.sizeof(header))
        C.memmove(pointer + C.sizeof(header), block, len(block))
        kernel.GlobalUnlock(handle)
        if not user.PostMessageW(int(widget.tree.winfo_id()), 0x0233, handle, 0):
            raise C.WinError(C.get_last_error())

    start = time.monotonic()
    second_sent = [False]

    def tick():
        ticks.append(time.monotonic())
        root.after(20, tick)

    def finish_when_ready():
        if time.monotonic() - start > 10:
            raise AssertionError("drop processing timed out")
        ready = not widget.busy and not widget.preparing
        if (
            scenario in ("repeat", "callback-error")
            and ready
            and len(events) == 1
            and not second_sent[0]
        ):
            second_sent[0] = True
            post()
        expected_events = (
            2 if scenario in ("repeat", "callback-error") else 0 if scenario == "limit" else 1
        )
        done = ready and (bool(limits) if scenario == "limit" else len(events) == expected_events)
        if done and time.monotonic() - start > 0.35:
            expected_handles = {
                int(control.winfo_id())
                for control in (
                    widget.sidebar_surface,
                    widget.sidebar,
                    widget.tree,
                    widget.empty_queue,
                )
            }
            actual_handles = {int(handle) for handle, _, _ in drop.hooks}
            root_handles = {
                int(root.winfo_id()),
                int(user.GetParent(root.winfo_id()) or root.winfo_id()),
            }
            result = {
                "events": events,
                "jobs": [job.path for job in widget.jobs],
                "chapters": [job.chapter_count for job in widget.jobs],
                "limits": len(limits),
                "warnings": len(warnings),
                "heartbeats": len(ticks),
                "native_messages": len(native_count),
                "tk_calls_in_native_callback": len(tk_reentries),
                "right_side_only": actual_handles == expected_handles,
                "root_accepts_drop": any(bool(get_style(h, -20) & 0x10) for h in root_handles),
                "body_accepts_drop": bool(get_style(int(widget.body.winfo_id()), -20) & 0x10),
                "all_targets_accept_drop": all(
                    bool(get_style(h, -20) & 0x10) for h in actual_handles
                ),
            }
            (folder / "result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            widget.close()
            return
        root.after(30, finish_when_ready)

    root.after(10, tick)
    root.after(80, post)
    root.after(150, finish_when_ready)
    root.mainloop()
    faulthandler.cancel_dump_traceback_later()
    result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    result["bridge_closed"] = drop.closed and drop.poll_id is None
    (folder / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@unittest.skipUnless(os.name == "nt", "native Windows drag-and-drop")
class NativeFileDropTests(unittest.TestCase):
    def probe(self, scenario):
        with tempfile.TemporaryDirectory() as folder:
            env = os.environ.copy()
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            env["ZBOOK_DATA_DIR"] = str(Path(folder) / "runtime")
            run = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(Path(__file__).resolve()),
                    "--native-probe",
                    folder,
                    scenario,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=22,
            )
            self.assertEqual(run.returncode, 0, run.stdout + "\n" + run.stderr)
            result = json.loads((Path(folder) / "result.json").read_text(encoding="utf-8"))
        self.assertGreater(result["heartbeats"], 5)
        self.assertEqual(result["tk_calls_in_native_callback"], 0)
        self.assertTrue(result["bridge_closed"])
        self.assertTrue(result["right_side_only"])
        self.assertTrue(result["all_targets_accept_drop"])
        self.assertFalse(result["root_accepts_drop"])
        self.assertFalse(result["body_accepts_drop"])
        return result

    def test_multiple_unicode_files_import_once_without_native_tk_reentry(self):
        result = self.probe("files")
        self.assertEqual(len(result["jobs"]), 3)
        self.assertEqual(result["events"][0], result["jobs"])
        self.assertEqual(result["chapters"], [2, 2, 2])

    def test_native_drop_accepts_folder_via_the_existing_background_import(self):
        result = self.probe("folder")
        self.assertEqual(len(result["jobs"]), 3)

    def test_native_drop_rejects_51_selected_files_without_processing_paths(self):
        result = self.probe("limit")
        self.assertEqual(result["jobs"], [])
        self.assertEqual(result["events"], [])
        self.assertEqual(result["limits"], 1)

    def test_native_folder_over_limit_is_rejected_as_a_whole(self):
        result = self.probe("large-folder")
        self.assertEqual(result["jobs"], [])
        self.assertEqual(result["warnings"], 1)

    def test_repeated_drop_keeps_interface_alive_and_does_not_duplicate_books(self):
        result = self.probe("repeat")
        self.assertEqual(len(result["events"]), 2)
        self.assertEqual(len(result["jobs"]), 3)

    def test_callback_error_does_not_stop_the_native_queue_poll(self):
        result = self.probe("callback-error")
        self.assertEqual(len(result["jobs"]), 3)
        self.assertEqual(result["native_messages"], 2)


if __name__ == "__main__":
    if "--native-probe" in sys.argv:
        native_probe(sys.argv[-2], sys.argv[-1])
    else:
        unittest.main(verbosity=2)
