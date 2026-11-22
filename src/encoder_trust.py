# SPDX-License-Identifier: AGPL-3.0-only
"""External encoders require a per-user approval of both name and contents."""

from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import os
import uuid

BUILTIN_HASH = "03b0e16a66053cf57a4a5d6f14847e7e62d695fb1ec9e3f400d23d21b311c0a1"
NAMES = {"kindling.exe", "kindling-cli-windows.exe", "kindlegen.exe", "ebook-convert.exe"}


def trust_file():
    base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else os.environ.get("XDG_CONFIG_HOME")
    return (
        Path(base or (Path.home() / (".config" if os.name != "nt" else "AppData/Local")))
        / "ZBook"
        / "trusted-encoder.json"
    )


def identity(value):
    path = Path(value).resolve(strict=True)
    if not path.is_file() or path.name.lower() not in NAMES:
        raise ValueError("请选择 Kindling、KindleGen 或 Calibre 的编码器文件。")
    with path.open("rb") as stream:
        return {"path": str(path), "sha256": hashlib.file_digest(stream, "sha256").hexdigest()}


def approve(selected):
    if identity(selected["path"]) != selected:
        raise ValueError("编码器在选择后已改变，请重新选择。")
    target = trust_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.with_name(f".{uuid.uuid4().hex}.tmp")
    with stage.open("x", encoding="utf-8") as stream:
        json.dump(selected, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(stage, target)


def expected_encoder(configured="", explicit=None):
    if not configured:
        path = Path(__file__).resolve().parent / "engines/kindling/kindling.exe"
        return {"path": str(path.resolve()), "sha256": BUILTIN_HASH}
    try:
        selected = (
            explicit
            if explicit is not None
            else json.loads(trust_file().read_text(encoding="utf-8"))
        )
        if (
            isinstance(selected, dict)
            and selected.get("path") == str(Path(configured).resolve())
            and isinstance(selected.get("sha256"), str)
        ):
            return selected
    except OSError, ValueError:
        pass
    raise ValueError("此编码器尚未经当前用户确认，请在设置中重新选择文件。")


@contextmanager
def verified_encoder(selected):
    path = Path(selected["path"])
    if not path.is_file():
        raise ValueError("找不到编码器，请在设置中重新选择文件。")
    if os.name == "nt":
        # Deny write/delete sharing until CreateProcess has opened the verified image.
        import _winapi
        import msvcrt

        handle = _winapi.CreateFile(str(path), 0x80000000, 1, 0, 3, 0x80, 0)
        stream = os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY), "rb")
    else:
        stream = path.open("rb")
    with stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != selected["sha256"]:
            raise ValueError(
                "编码器内容已改变。外部编码器请重新选择；内置编码器请重新解压已校验的发布包。"
            )
        yield str(path)


def find_converter(configured=""):
    with verified_encoder(expected_encoder(configured)) as path:
        return path
