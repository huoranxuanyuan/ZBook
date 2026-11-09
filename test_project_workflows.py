# SPDX-License-Identifier: AGPL-3.0-only
"""Regressions for test isolation and complete public source packages."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from build_release import source_files
from tools.release import safe_relative

ROOT = Path(__file__).resolve().parent


class PublicProjectTests(unittest.TestCase):
    def test_importing_test_modules_preserves_explicit_data_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            expected = Path(temporary) / "explicit-runtime"
            env = os.environ.copy()
            env.update(ZBOOK_DATA_DIR=str(expected), PYTHONDONTWRITEBYTECODE="1")
            program = (
                "import json,os; before=os.environ['ZBOOK_DATA_DIR']; "
                "import test_cover_search_states,test_reading_layout; import paths; "
                "print(json.dumps([before,os.environ['ZBOOK_DATA_DIR'],str(paths.ROOT)]))"
            )
            result = subprocess.run(
                [sys.executable, "-B", "-c", program],
                cwd=ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
            self.assertEqual(json.loads(result.stdout), [str(expected)] * 3)

    def test_publication_contains_development_tools_and_user_documentation(self):
        names = {path.relative_to(ROOT).as_posix() for path in source_files()}
        required = {
            "tools/check.py",
            "tools/release.py",
            "docs/USER_GUIDE.md",
            "docs/ARCHITECTURE.md",
            "docs/images/main-window.png",
            "examples/novel.txt",
            "pyproject.toml",
            "requirements-dev.txt",
            "third-party-sources.json",
        }
        self.assertTrue(required.issubset(names), required - names)
        self.assertFalse(any(name.startswith(("data/", "archive/", ".git/")) for name in names))

    def test_release_paths_cannot_escape_the_release_directory(self):
        for value in ("../private.txt", "/private.txt", "C:/private.txt", "a\\private.txt"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                safe_relative(value)
        self.assertEqual(safe_relative("ZBook-1.0.0-source.zip"), "ZBook-1.0.0-source.zip")


if __name__ == "__main__":
    unittest.main()
