# SPDX-License-Identifier: AGPL-3.0-only
"""Compare installed Windows dependencies to the audited wheel bytes before building."""

from pathlib import Path
import argparse
import hashlib
from importlib import metadata
import json
import zipfile


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_wheels(root, directory):
    lock = json.loads((root / "windows-wheels.json").read_text(encoding="utf-8"))
    checked = {}
    for package, entry in lock.items():
        wheel = directory / entry["file"]
        if digest(wheel) != entry["sha256"]:
            raise ValueError(f"Incorrect wheel hash for {package}")
        dist = metadata.distribution(package)
        if dist.version != entry["version"]:
            raise ValueError(f"Incorrect installed version for {package}")
        count = 0
        with zipfile.ZipFile(wheel) as archive:
            for item in archive.infolist():
                name = item.filename
                if item.is_dir() or name.endswith(".dist-info/RECORD"):
                    continue
                parts = name.split("/")
                if parts[0].endswith(".data"):
                    if parts[1] not in {"purelib", "platlib"}:
                        continue
                    name = "/".join(parts[2:])
                path = Path(dist.locate_file(name))
                with archive.open(item) as contents:
                    expected = hashlib.file_digest(contents, "sha256").hexdigest()
                if not path.is_file() or digest(path) != expected:
                    raise ValueError(
                        f"Installed content differs from the official wheel: {package}/{name}"
                    )
                count += 1
        checked[package] = {
            "wheel": entry["file"],
            "sha256": entry["sha256"],
            "files_verified": count,
        }
    return checked


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel_directory", type=Path)
    print(
        json.dumps(
            verify_wheels(Path(__file__).resolve().parents[1], parser.parse_args().wheel_directory),
            indent=2,
        )
    )
