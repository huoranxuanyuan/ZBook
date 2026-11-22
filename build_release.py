# SPDX-License-Identifier: AGPL-3.0-only
"""Build a clean Windows x64 release from an explicit publication allowlist."""

from pathlib import Path
import argparse, datetime, hashlib, importlib.metadata as metadata, json, os, shutil, subprocess, sys, zipfile
from branding import VERSION, AUTHOR, COPYRIGHT, ICON_FILE

ROOT = Path(__file__).resolve().parent
from publication import publication, PORTABLE_GENERATED
from tools.verify_wheels import verify_wheels


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_files():
    return publication(ROOT)[0]


def source_revision():
    """Identify a local checkout; source archives remain buildable without Git."""
    if not (ROOT / ".git").exists():
        return None
    executable = shutil.which("git")
    if not executable:
        raise ValueError("Git is required to validate this checkout")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}

    def git(*args):
        return (
            subprocess.run(
                [executable, *args], cwd=ROOT, env=env, check=True, capture_output=True, timeout=15
            )
            .stdout.decode("utf-8")
            .strip()
        )

    try:
        if Path(git("rev-parse", "--show-toplevel")).resolve() != ROOT:
            raise ValueError("Unexpected Git work tree")
        return {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain"))}
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("Cannot verify Git status for this checkout") from exc


def native_versions():
    import pymupdf, ssl

    if (pymupdf.VersionBind, pymupdf.VersionFitz) != ("2.1", "1.28.6"):
        raise ValueError("Unexpected native PDF runtime")
    return {
        "PyMuPDF-Lite": pymupdf.VersionBind,
        "MuPDF": pymupdf.VersionFitz,
        "Brotli (MuPDF source)": "1.2.1",
        "OpenSSL": ssl.OPENSSL_VERSION,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=ROOT.parent / "releases" / VERSION)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument(
        "--wheel-dir",
        type=Path,
        default=ROOT / ".wheels",
        help="Directory containing the locked Windows wheels",
    )
    args = parser.parse_args()
    release = args.dist_dir.resolve()
    if release.exists() and any(release.iterdir()):
        raise SystemExit(
            "Release directory must be empty; existing releases are never overwritten."
        )
    release.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    work = args.work_dir or ROOT / "archive" / "builds" / (VERSION + "-" + stamp)
    work = work.resolve()
    work.mkdir(parents=True, exist_ok=False)
    revision = source_revision()
    if revision and revision["dirty"]:
        raise ValueError("Commit reviewed source changes before building a release")
    inputs, portable_names = publication(ROOT)
    wheel_digests = verify_wheels(ROOT, args.wheel_dir)
    source_manifest = {p.relative_to(ROOT).as_posix(): digest(p) for p in inputs}
    engine = ROOT / "engines/kindling/kindling.exe"
    origin = json.loads(engine.with_name("origin.json").read_text(encoding="utf-8"))
    if digest(engine) != origin["sha256"]:
        raise ValueError("Encoder integrity check failed")
    icon = work / "zbook.ico"
    shutil.copy2(ICON_FILE, icon)
    version_tuple = tuple(map(int, VERSION.split("."))) + (0,)
    version_file = work / "version-info.txt"
    version_file.write_text(
        f"VSVersionInfo(ffi=FixedFileInfo(filevers={version_tuple!r},prodvers={version_tuple!r},mask=0x3f,flags=0,OS=0x40004,fileType=1,subtype=0,date=(0,0)),kids=[StringFileInfo([StringTable('080404b0',[StringStruct('FileDescription','ZBook 小说转换器'),StringStruct('FileVersion',{VERSION!r}),StringStruct('ProductName','ZBook 小说转换器'),StringStruct('ProductVersion',{VERSION!r}),StringStruct('CompanyName',{AUTHOR!r}),StringStruct('LegalCopyright',{COPYRIGHT!r})])]),VarFileInfo([VarStruct('Translation',[2052,1200])])])",
        encoding="utf-8",
    )
    portable = release / ("ZBook-" + VERSION + "-Windows-x64")
    portable.mkdir()
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--noupx",
        "--name",
        "ZBook",
        "--icon",
        str(icon),
        "--paths",
        str(ROOT),
        "--distpath",
        str(portable),
        "--workpath",
        str(work / "work"),
        "--specpath",
        str(work),
        "--add-binary",
        str(engine) + ";engines/kindling",
        # Editable masters are included in the source ZIP. The executable
        # needs the optical PNGs loaded by branding.py and the native ICO.
        *[
            argument
            for path in inputs
            if path.parent == ICON_FILE.parent
            and (
                path.name == "zbook.ico"
                or path.suffix == ".png"
                and path.name.startswith(("app-", "mark-"))
            )
            for argument in ("--add-data", str(path) + ";assets/branding")
        ],
        "--version-file",
        str(version_file),
    ]
    for excluded in [
        "PySide6",
        "PyQt6",
        "PyQt5",
        "numpy",
        "pandas",
        "matplotlib",
        "scipy",
        "cv2",
        "IPython",
        "pytest",
        "notebook",
        "tkinter.test",
        "PIL.AvifImagePlugin",
        "PIL._avif",
    ]:
        command += ["--exclude-module", excluded]
    packages = [
        "Pillow",
        "requests",
        "urllib3",
        "certifi",
        "idna",
        "charset-normalizer",
        "PyMuPDF-Lite",
        "olefile",
        "striprtf",
        "regex",
        "PySocks",
    ]
    for package in packages:
        dist = metadata.distribution(package)
        meta = next(
            f for f in dist.files if f.name == "METADATA" and f.parent.name.endswith(".dist-info")
        )
        command += ["--add-data", str(dist.locate_file(meta)) + ";" + meta.parent.name]
    command.append(str(ROOT / "app.py"))
    env = os.environ.copy()
    env["TMP"] = env["TEMP"] = str(work)
    env["ZBOOK_DATA_DIR"] = str(work / "runtime")
    env["PYTHONHASHSEED"] = "0"
    with (work / "build.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError((work / "build.log").read_text(encoding="utf-8")[-5000:])
    exe = portable / "ZBook.exe"
    if exe.stat().st_size > 50_000_000:
        raise ValueError("Portable executable exceeded the 50 MB size budget")
    from PyInstaller.archive.readers import CArchiveReader

    reader = CArchiveReader(str(exe))
    entries = list(reader.toc)
    if any(
        any(
            word in name.lower()
            for word in ("pyside", "qt6core", "webengine", "calibre", "kindlegen.exe", "_avif")
        )
        for name in entries
    ):
        raise ValueError("Unexpected bundled engine or component")
    if any(name.lower().endswith("direct_url.json") for name in entries):
        raise ValueError("Local installer provenance must not be published")
    import pymupdf, ssl

    native_files = {
        p.name: p
        for p in Path(pymupdf.__file__).parent.iterdir()
        if p.suffix.lower() in (".pyd", ".dll")
    }
    dlls = Path(ssl._ssl.__file__).parent
    native_files.update({n: dlls / n for n in ("_ssl.pyd", "libssl-3.dll", "libcrypto-3.dll")})
    native_digests = {}
    for name, path in native_files.items():
        matches = [n for n in entries if Path(n).name.casefold() == name.casefold()]
        if len(matches) != 1 or hashlib.sha256(reader.extract(matches[0])).hexdigest() != digest(
            path
        ):
            raise ValueError(f"Frozen native library differs from the verified runtime: {name}")
        native_digests[name] = digest(path)
    manifest = {
        "version": VERSION,
        "author": AUTHOR,
        "copyright": COPYRIGHT,
        "license": "AGPL-3.0-only",
        "source_revision": revision,
        "built_utc": stamp,
        "python": sys.version.split()[0],
        "dependencies": {name: metadata.version(name) for name in packages},
        "encoder": origin,
        "native": native_versions(),
        "native_sha256": native_digests,
        "windows_wheels": wheel_digests,
        "executable": {"file": "ZBook.exe", "bytes": exe.stat().st_size, "sha256": digest(exe)},
        "source_manifest": source_manifest,
        "bundled_entries": entries,
    }
    for name in portable_names:
        target = portable / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    (portable / "release-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with zipfile.ZipFile(
        release / ("ZBook-" + VERSION + "-source.zip"), "w", zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in inputs:
            archive.write(path, "ZBook-source/" + path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(
        release / ("ZBook-" + VERSION + "-Windows-x64.zip"),
        "w",
        zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for name in sorted(set(portable_names) | PORTABLE_GENERATED):
            archive.write(portable / name, portable.name + "/" + name)
    print(
        json.dumps(
            {"portable": str(portable), "exe": manifest["executable"], "work": str(work)},
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
