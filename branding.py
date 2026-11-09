# SPDX-License-Identifier: AGPL-3.0-only
from PIL import Image, ImageDraw

VERSION = "1.0.0"
APP_NAME = "ZBook 小说转换器"
AUTHOR = "huoranxuanyuan"
COPYRIGHT = f"Copyright (C) 2026 {AUTHOR}"


def application_icon():
    image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((12, 12, 244, 244), radius=55, fill="#007aff")
    draw.rounded_rectangle((58, 52, 200, 206), radius=13, fill="white")
    draw.rectangle((68, 52, 78, 206), fill="#dbeaff")
    draw.line(
        [(101, 89), (171, 89), (101, 158), (171, 158)], fill="#007aff", width=13, joint="curve"
    )
    return image
