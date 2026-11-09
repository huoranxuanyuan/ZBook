# SPDX-License-Identifier: AGPL-3.0-only
"""Verify a built release, attach locked third-party sources and write checksums."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))

from branding import AUTHOR, VERSION
from build_release import source_files, source_revision


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def safe_relative(name: str) -> str:
    value = PurePosixPath(name)
    if not name or "\\" in name or ":" in name or value.is_absolute() or ".." in value.parts:
        raise ValueError(f"Invalid archive path: {name}")
    return value.as_posix()


def verify_project(release: Path) -> dict:
    portable = release / f"ZBook-{VERSION}-Windows-x64"
    manifest = load_json(portable / "release-manifest.json")
    if manifest.get("version") != VERSION or manifest.get("author") != AUTHOR:
        raise ValueError("The release version or author does not match this source tree")
    if manifest.get("license") != "AGPL-3.0-only":
        raise ValueError("The project license is missing or incorrect")
    expected = manifest["source_manifest"]
    if set(expected) != {path.relative_to(ROOT).as_posix() for path in source_files()}:
        raise ValueError("The publication allowlist and source manifest differ")
    for name, value in expected.items():
        name = safe_relative(name)
        if not re.fullmatch("[0-9a-f]{64}", value) or digest(ROOT / name) != value:
            raise ValueError(f"Source hash mismatch: {name}")
    revision = source_revision()
    if revision:
        if revision["dirty"] or manifest.get("source_revision") != revision:
            raise ValueError("The release must match a clean current Git commit")
        executable = shutil.which("git")
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        tracked = subprocess.run(
            [executable, "ls-files", "-z"], cwd=ROOT, env=env, check=True, capture_output=True
        ).stdout.decode("utf-8")
        if {name for name in tracked.split("\0") if name} != set(expected):
            raise ValueError("Tracked files and the published source package differ")
    exe = portable / "ZBook.exe"
    if digest(exe) != manifest["executable"]["sha256"] or exe.stat().st_size > 50_000_000:
        raise ValueError("Executable hash or size check failed")
    with zipfile.ZipFile(release / f"ZBook-{VERSION}-source.zip") as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != {
            "ZBook-source/" + name for name in expected
        }:
            raise ValueError("Unexpected or duplicate source ZIP members")
        for name, value in expected.items():
            if hashlib.sha256(archive.read("ZBook-source/" + name)).hexdigest() != value:
                raise ValueError(f"Source ZIP mismatch: {name}")
    with zipfile.ZipFile(release / f"ZBook-{VERSION}-Windows-x64.zip") as archive:
        files = {
            path.relative_to(portable).as_posix(): path
            for path in portable.rglob("*")
            if path.is_file()
        }
        names = archive.namelist()
        prefix = portable.name + "/"
        if len(names) != len(set(names)) or set(names) != {prefix + name for name in files}:
            raise ValueError("Portable ZIP members do not match the portable directory")
        if any(name.startswith(("data/", "archive/", ".git/")) for name in files):
            raise ValueError("Runtime or Git data must not be published")
        for name, path in files.items():
            if hashlib.sha256(archive.read(prefix + name)).hexdigest() != digest(path):
                raise ValueError(f"Portable ZIP mismatch: {name}")
    return manifest


def smoke_test(release: Path, output: Path) -> dict:
    if os.name != "nt":
        raise ValueError("The frozen Windows program must be tested on Windows")
    output.mkdir(parents=True, exist_ok=False)
    source = output / "《公开验收样例》作者：示例作者.txt"
    source.write_text(
        "第一卷 开端\n第一章 初见\n中文 English & <script> 均应保留。\n"
        "第二章 启程\n正文。\n第二卷 归途\n第一章 再会\n正文。\n"
        "第二章 末尾\n末段标记：完整转换结束。",
        encoding="utf-8",
    )
    from PIL import Image

    Image.new("RGB", (240, 360), "#377f79").save(source.with_suffix(".png"))
    portable = release / f"ZBook-{VERSION}-Windows-x64"
    env = os.environ.copy()
    env.update(ZBOOK_DATA_DIR=str(output / "runtime"), TEMP=str(output), TMP=str(output))
    subprocess.run(
        [str(portable / "ZBook.exe"), "--smoke-test", str(source), str(output / "output")],
        cwd=portable,
        env=env,
        timeout=120,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    report = load_json(output / "output" / "portable-verification.json")
    result = report["report"]["results"][0]
    if not report["frozen"] or result["status"] != "完成" or result["chapters"] != 4:
        raise ValueError("The frozen conversion did not complete all sample chapters")
    epub = next(Path(name) for name in result["outputs"] if name.endswith(".epub"))
    mobi = next(Path(name) for name in result["outputs"] if name.endswith(".mobi"))
    with zipfile.ZipFile(epub) as archive:
        for name in archive.namelist():
            if name.endswith((".xhtml", ".opf", ".ncx")):
                ET.fromstring(archive.read(name))
        body = "".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.startswith("OEBPS/chapter") and name.endswith(".xhtml")
        )
        metadata = ET.fromstring(archive.read("OEBPS/content.opf"))
        authors = [
            node.text for node in metadata.findall(".//{http://purl.org/dc/elements/1.1/}creator")
        ]
        if authors != ["示例作者"] or "OEBPS/cover.jpg" not in archive.namelist():
            raise ValueError("The book author or cover was not preserved")
        if "末段标记：完整转换结束。" not in body or "&lt;script&gt;" not in body:
            raise ValueError("EPUB text preservation check failed")
    import pymupdf

    with pymupdf.open(mobi) as document:
        text = "".join(page.get_text() for page in document)
    if "末段标记：完整转换结束。" not in re.sub(r"\s+", "", text):
        raise ValueError("MOBI text preservation check failed")
    dump = subprocess.run(
        [str(ROOT / "engines/kindling/kindling.exe"), "dump", str(mobi)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    ).stdout
    markers = ["mobi.file_version = 6", "mobi.file_version = 8", 'exth[201].name = "coveroffset"']
    if not all(marker in dump for marker in markers):
        raise ValueError("MOBI7, KF8 or cover metadata is missing")
    (output / "mobi-structure.txt").write_text(dump, encoding="utf-8")
    return {
        "epub": True,
        "mobi7_and_kf8": True,
        "cover": True,
        "book_author": authors[0],
        "output": str(output),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--third-party-sources", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument(
        "--smoke", action="store_true", help="Test the actual EXE using synthetic local input"
    )
    parser.add_argument("--report-dir", type=Path, help="New directory for validation reports")
    args = parser.parse_args()
    release = args.release_dir.resolve()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (
        args.report_dir or ROOT / "archive/release-checks" / f"{stamp}-{uuid.uuid4().hex[:8]}"
    ).resolve()
    if output.exists():
        parser.error("Use a new report directory to preserve previous verification results")
    output.mkdir(parents=True)
    try:
        manifest = verify_project(release)
        lock = load_json(ROOT / "third-party-sources.json")
        if lock["version"] != VERSION:
            raise ValueError("The third-party source lock does not match this version")
        target = release / safe_relative(lock["file"])
        supplied = args.third_party_sources.resolve() if args.third_party_sources else target
        if digest(supplied) != lock["sha256"] or supplied.stat().st_size != lock["bytes"]:
            raise ValueError("Third-party source archive integrity check failed")
        if not args.verify_only and supplied != target:
            if target.exists():
                if digest(target) != lock["sha256"]:
                    raise ValueError("An existing third-party archive must not be replaced")
            else:
                shutil.copy2(supplied, target)
        if not target.is_file() or digest(target) != lock["sha256"]:
            raise ValueError("The locked third-party source archive is missing from the release")
        portable = release / f"ZBook-{VERSION}-Windows-x64"
        files = [
            release / f"ZBook-{VERSION}-Windows-x64.zip",
            release / f"ZBook-{VERSION}-source.zip",
            target,
            portable / "ZBook.exe",
        ]
        smoke = smoke_test(release, output / "smoke") if args.smoke else None
        checksums = "".join(
            f"{digest(path)}  {path.relative_to(release).as_posix()}\n" for path in files
        )
        checksum_file = release / "SHA256SUMS.txt"
        if checksum_file.exists() or args.verify_only:
            if checksum_file.read_text(encoding="utf-8") != checksums:
                raise ValueError("The existing checksum list differs from the release files")
        elif not args.verify_only:
            checksum_file.write_text(checksums, encoding="utf-8")
        summary = {
            "version": VERSION,
            "author": AUTHOR,
            "source_revision": manifest.get("source_revision"),
            "source_files": len(manifest["source_manifest"]),
            "success": True,
            "release": str(release),
            "smoke": smoke,
        }
        (output / "verification.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        failure = {"success": False, "error": str(exc)}
        (output / "verification.json").write_text(
            json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Release verification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
