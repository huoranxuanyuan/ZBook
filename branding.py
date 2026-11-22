# SPDX-License-Identifier: AGPL-3.0-only
"""Names and self-contained application/toolbar icon resources."""

from functools import lru_cache
from pathlib import Path

from PIL import Image

VERSION = "1.0.0"
APP_NAME = "ZBook 小说转换器"
AUTHOR = "huoranxuanyuan"
COPYRIGHT = f"Copyright (C) 2026 {AUTHOR}"
ASSET_DIR = Path(__file__).resolve().parent / "assets" / "branding"
ICON_FILE = ASSET_DIR / "zbook.ico"
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256, 512, 1024)


MARK_SIZES = tuple(sorted({*ICON_SIZES, 26, 30, 60}))


@lru_cache(maxsize=64)
def _icon_resource(kind, size):
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 2048:
        raise ValueError("Icon size must be an integer between 1 and 2048")
    sizes = ICON_SIZES if kind == "app" else MARK_SIZES
    source_size = next((value for value in sizes if value >= size), sizes[-1])
    with Image.open(ASSET_DIR / f"{kind}-{source_size}.png") as source:
        image = source.convert("RGBA")
    if source_size != size:
        image = image.resize((size, size), Image.Resampling.LANCZOS)
    return image


def application_icon(size=256):
    """Return the optical-size asset; callers receive an independent RGBA image."""
    return _icon_resource("app", size).copy()


def brand_mark(size=32, *, appearance="light"):
    """Return the transparent page-to-reader mark for the requested surface."""
    kinds = {"light": "mark-blue", "dark": "mark-white", "mono": "mark-ink"}
    if appearance not in kinds:
        raise ValueError("Unknown icon appearance: " + str(appearance))
    return _icon_resource(kinds[appearance], size).copy()
