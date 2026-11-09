# SPDX-License-Identifier: AGPL-3.0-only
"""Decode only the raster formats offered by the cover UI."""
from pathlib import Path
from PIL import Image

LOCAL_FORMATS=('JPEG','PNG','WEBP','BMP')
NETWORK_FORMATS=('JPEG','PNG','WEBP')

def open_image(source,formats=LOCAL_FORMATS):
    if isinstance(source,(str,Path)) and Path(source).stat().st_size>20_000_000:
        raise ValueError('封面文件超过 20 MB，请先缩小图片')
    image=Image.open(source,formats=formats)
    if image.width*image.height>28_000_000:
        image.close()
        raise ValueError('封面像素过大，请先缩小图片')
    return image
