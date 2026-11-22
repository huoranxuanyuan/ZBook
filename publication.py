# SPDX-License-Identifier: AGPL-3.0-only
"""Validate the reviewed exact list used by source ZIP, portable ZIP and verifier."""

from pathlib import Path, PurePosixPath
import os

PORTABLE_GENERATED = {"ZBook.exe", "release-manifest.json"}
LOCAL_DIRECTORIES = {
    ".git",
    ".serena",
    "__pycache__",
    "data",
    "archive",
    "output",
    "tests",
    "fixtures",
    "examples",
    "local-dev",
    "maintenance",
}


def publication(root):
    listing = root / "release-files.txt"
    if already_linked(listing):
        raise ValueError("Invalid release allowlist location")
    source, portable, keys = [], [], set()
    for row in listing.read_text(encoding="utf-8").splitlines():
        if not row or row.startswith("#"):
            continue
        mode, separator, name = row.partition(" ")
        value = PurePosixPath(name)
        if (
            not separator
            or mode not in {"source", "both"}
            or not name
            or name != value.as_posix()
            or value.is_absolute()
            or "\\" in name
            or ":" in name
            or ".." in value.parts
            or name.casefold() in keys
            or any(
                part.casefold() in LOCAL_DIRECTORIES or part.endswith((" ", "."))
                for part in value.parts
            )
            or value.suffix.lower()
            in {".epub", ".mobi", ".azw", ".azw3", ".pfx", ".key", ".sqlite", ".sqlite3", ".log"}
            or value.name.casefold() in {"agents.md", "portable_smoke.py"}
            or (value.suffix.lower() == ".py" and value.stem.casefold().startswith("test_"))
            or name in PORTABLE_GENERATED
        ):
            raise ValueError(f"Invalid release allowlist entry: {row}")
        path = root / value
        if (
            any(
                already_linked(p)
                for p in (path, *path.parents)
                if p != root and p.is_relative_to(root)
            )
            or not path.is_file()
        ):
            raise ValueError(f"Missing or linked release input: {name}")
        keys.add(name.casefold())
        source.append(path)
        if mode == "both":
            portable.append(name)
    if listing not in source:
        raise ValueError("The release allowlist must include itself")
    return sorted(source), sorted(portable)


def already_linked(path):
    return path.is_symlink() or (os.name == "nt" and bool(path.lstat().st_file_attributes & 0x400))
