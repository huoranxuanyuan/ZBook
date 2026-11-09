# SPDX-License-Identifier: AGPL-3.0-only
"""Run offline regressions in an isolated directory and retain test artifacts."""

from __future__ import annotations

import argparse
import contextlib
import datetime
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, help="New directory for reports and retained data"
    )
    args = parser.parse_args()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (
        args.output_dir or ROOT / "archive" / "checks" / f"{stamp}-{uuid.uuid4().hex[:8]}"
    ).resolve()
    if output.exists():
        parser.error("The report directory must not exist; previous results are never overwritten")
    output.mkdir(parents=True)
    temporary = output / "temporary"
    temporary.mkdir()
    os.environ.update(
        ZBOOK_DATA_DIR=str(output / "runtime"),
        PYTHONDONTWRITEBYTECODE="1",
        TMP=str(temporary),
        TEMP=str(temporary),
    )
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    tempfile.tempdir = str(temporary)

    class RetainedTemporaryDirectory:
        """Match the context-manager API while keeping evidence for inspection."""

        def __init__(self, suffix=None, prefix=None, dir=None, **kwargs):
            parent = Path(dir or temporary).resolve()
            if not parent.is_relative_to(output):
                raise ValueError("Tests may only create temporary directories inside this run")
            self.name = tempfile.mkdtemp(suffix=suffix, prefix=prefix, dir=parent)

        def __enter__(self):
            return self.name

        def __exit__(self, *args):
            self.cleanup()

        def cleanup(self):
            pass

    def archive_test_data(paths):
        target = output / "retained-cleanup"
        target.mkdir(exist_ok=True)
        for value in paths:
            path = Path(value).resolve()
            if not path.is_relative_to(output):
                raise OSError("Refusing to clean anything outside this test run")
            if path.exists():
                shutil.move(str(path), str(target / f"{uuid.uuid4().hex}-{path.name}"))

    attempts = []

    def block_network(*args, **kwargs):
        attempts.append("A direct network operation was blocked")
        raise OSError("External network is disabled during regression tests")

    import cache_maintenance

    log = io.StringIO()
    with (
        patch.object(tempfile, "TemporaryDirectory", RetainedTemporaryDirectory),
        patch.object(cache_maintenance, "native_recycle", archive_test_data),
        patch.object(socket.socket, "connect", block_network),
        patch.object(socket.socket, "connect_ex", block_network),
        patch.object(socket, "getaddrinfo", block_network),
        contextlib.redirect_stdout(log),
        contextlib.redirect_stderr(log),
    ):
        suite = unittest.defaultTestLoader.discover(str(ROOT), pattern="test_*.py")
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)

    (output / "regression.log").write_text(log.getvalue(), encoding="utf-8")
    summary = {
        "tests": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "success": result.wasSuccessful(),
        "blocked_network_attempts": len(attempts),
        "temporary_data_retained": True,
        "test_cleanup": "move within isolated report directory",
        "report_directory": str(output),
    }
    (output / "regression.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if not result.wasSuccessful():
        print(log.getvalue()[-12000:])
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
