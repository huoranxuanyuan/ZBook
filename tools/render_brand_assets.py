# SPDX-License-Identifier: AGPL-3.0-only
"""Re-export C / page to Kindle from the approved artwork in assets/branding."""

import argparse
import base64
import hashlib
import json
import shutil
from pathlib import Path

import pymupdf
from PIL import Image

SOURCE = Path(__file__).resolve().parents[1] / "assets" / "branding"
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256, 512, 1024)
MARK_SIZES = sorted({*SIZES, 26, 30, 60})
NATIVE_MARKS = (26, 30, 60)
AMBER = "#EDBD7E"
DARK = "#38414B"
TILE = (
    "M84 4H172C202 4 217 7 231 21C246 35 252 52 252 84V172"
    "C252 203 247 220 232 235C218 249 201 252 172 252H84"
    "C54 252 38 249 24 235C9 221 4 204 4 172V84"
    "C4 52 10 35 25 21C39 7 54 4 84 4Z"
)


def svg_png(svg, size):
    with pymupdf.open(stream=svg.encode("utf-8"), filetype="svg") as doc:
        page = doc[0]
        scale = 2 * size / page.rect.width
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=True)
        im = Image.frombytes("RGBa", (pix.width, pix.height), pix.samples).convert("RGBA")
    return im.resize((size, size), Image.Resampling.LANCZOS)


def desktop_svg(background, embedded):
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        'width="1024" height="1024" viewBox="0 0 256 256"><title>ZBook · 纸页入屏</title>'
        + background
        + '<image width="256" height="256" xlink:href="data:image/png;base64,'
        + embedded
        + '"/></svg>'
    )


def export(out):
    out.mkdir(parents=True, exist_ok=True)
    masters = [
        "app-1024.png",
        "zbook.ico",
        "page-turn.png",
        "app-small.svg",
        "mark-blue.svg",
        "mark-white.svg",
        "mark-ink.svg",
        *[f"mark-blue-{n}.png" for n in NATIVE_MARKS],
    ]
    if out.resolve() != SOURCE.resolve():
        for name in masters:
            shutil.copyfile(SOURCE / name, out / name)
    with Image.open(out / "zbook.ico") as ico:
        for n, _ in sorted(ico.ico.sizes()):
            ico.ico.getimage((n, n)).convert("RGBA").save(out / f"app-{n}.png")
    with Image.open(out / "app-1024.png") as master:
        master.resize((512, 512), Image.Resampling.LANCZOS).save(out / "app-512.png")
        tile_alpha = master.getchannel("A")
    with Image.open(out / "page-turn.png") as artwork:
        embedded = base64.b64encode((out / "page-turn.png").read_bytes()).decode("ascii")
        for name, color in (("app", AMBER), ("app-dark", DARK), ("fullbleed", AMBER)):
            shape = (
                f'<rect width="256" height="256" fill="{color}"/>'
                if name == "fullbleed"
                else f'<path d="{TILE}" fill="{color}"/>'
            )
            (out / f"{name}.svg").write_text(desktop_svg(shape, embedded), encoding="utf-8")
            if name != "app":
                layer = Image.new("RGBA", artwork.size, color)
                if name == "app-dark":
                    layer.putalpha(tile_alpha)
                layer.alpha_composite(artwork)
                layer.save(out / f"{name}-1024.png")
    for name in ("mark-blue", "mark-white", "mark-ink"):
        svg = (out / f"{name}.svg").read_text(encoding="utf-8")
        for n in MARK_SIZES:
            if name == "mark-blue" and n in NATIVE_MARKS:
                continue
            svg_png(svg, n).save(out / f"{name}-{n}.png")
    mono = (out / "mark-ink.svg").read_text(encoding="utf-8")
    (out / "mark.svg").write_text(mono.replace("#243247", "currentColor"), encoding="utf-8")
    manifest = {
        "design": "C / 纸页入屏 · 原案杏",
        "sizes": SIZES,
        "mark_sizes": MARK_SIZES,
        "micro_sizes": [16, 20],
        "files": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(out.iterdir())
            if p.is_file() and p.name != "asset-manifest.json"
        },
    }
    (out / "asset-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=SOURCE)
    parser.add_argument("--preview-dir", type=Path)
    args = parser.parse_args()
    export(args.output_dir)
    if args.preview_dir:
        args.preview_dir.mkdir(parents=True, exist_ok=True)
        canvas = Image.new("RGBA", (570, 300), "#F4F5F7")
        for name, xy in (
            ("app-256", (10, 22)),
            ("app-48", (288, 55)),
            ("app-32", (356, 63)),
            ("app-16", (410, 71)),
            ("mark-blue-30", (288, 170)),
            ("mark-ink-30", (353, 170)),
        ):
            with Image.open(args.output_dir / f"{name}.png") as im:
                canvas.alpha_composite(im, xy)
        canvas.save(args.preview_dir / "ZBook-C-adaptation.png")


if __name__ == "__main__":
    main()
