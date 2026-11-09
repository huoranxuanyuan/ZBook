# SPDX-License-Identifier: AGPL-3.0-only
"""EPUB 3 authoring with NCX compatibility and explicit cover metadata."""
from __future__ import annotations
from datetime import datetime, timezone
from html import escape
from io import BytesIO
from pathlib import Path
from safe_image import open_image
import re
import uuid
import zipfile
from xml.etree import ElementTree as ET
from PIL import Image, ImageOps, ImageDraw, ImageFont
from core import Chapter, ParsedBook
from cover_design import generated_cover,font
from reading_style import LINE_HEIGHT, PARAGRAPH_GAP

CSS = '''body {font-size: 1em; line-height: 1.2; padding: 0; margin: 0 5pt;}
h1 {display: block; font-size: 1.2em; font-weight: bold; text-align: left; text-indent: 0; margin: 0em auto; line-height: 1.2; border: 0;}
p {display: block; font-size: 1em; text-indent: 2em; margin: 0em auto; line-height: 1.2; border: 0; overflow-wrap: break-word;}
.cover {margin: 0; text-align: center;} .cover img {max-width: 100%; max-height: 98vh;}
nav ol {list-style-type: none; padding-left: 1em;} nav li {margin: .4em 0;}
'''



def cover_bytes(path, title, author, cover_style='modern'):
    if not path:
        return generated_cover(title, author, style=cover_style)
    with open_image(path) as im:
        if im.width * im.height > 32_000_000:
            raise ValueError('封面像素过大，请先缩小图片')
        im = ImageOps.exif_transpose(im)
        if im.mode in ('RGBA', 'LA') or 'transparency' in im.info:
            bg = Image.new('RGBA', im.size, 'white')
            bg.alpha_composite(im.convert('RGBA'))
            im = bg.convert('RGB')
        else:
            im = im.convert('RGB')
        im.thumbnail((1800, 2700))
        out = BytesIO()
        im.save(out, 'JPEG', quality=92)
        return out.getvalue()

def xhtml(title, body):
    return f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh-CN" lang="zh-CN">
<head><title>{escape(title)}</title><meta charset="utf-8"/><link href="style.css" rel="stylesheet" type="text/css"/></head>
<body>{body}</body></html>'''

def _pages(chapters):
    pages = []
    for ch in chapters:
        parts, current, size = [], [], 0
        # Split only on paragraph boundaries, except pathological unbroken lines.
        for line in ch.text.split('\n'):
            chunks = [line[i:i + 80000] for i in range(0, len(line), 80000)] or ['']
            for chunk in chunks:
                if size > 100000:
                    parts.append('\n'.join(current));current=[];size=0
                current.append(chunk);size += len(chunk) + 1
        parts.append('\n'.join(current))
        for i, part in enumerate(parts):
            title = ch.title if i == 0 else f'{ch.title}（续 {i}）'
            pages.append(Chapter(title, part, ch.line, ch.level, ch.kind))
    return pages

def _toc_tree(pages):
    roots = []
    volume = None
    for index, chapter in enumerate(pages):
        node = {'title': chapter.title, 'href': f'chapter{index + 1:05}.xhtml', 'children': []}
        if chapter.kind == 'volume':
            roots.append(node);volume=node
        elif chapter.level == 2 and volume is not None:
            volume['children'].append(node)
        else:
            roots.append(node)
    return roots

def write_epub(book: ParsedBook, target, cover_path='', line_height=LINE_HEIGHT, paragraph_gap=PARAGRAPH_GAP, cover_style='modern'):
    if not .9 <= float(line_height) <= 3 or not 0 <= float(paragraph_gap) <= 3:
        raise ValueError('行距或段距超出允许范围')
    target = Path(target)
    pages = _pages(book.chapters)
    toc = _toc_tree(pages)
    start_index=next((i for i,ch in enumerate(pages,1) if ch.kind=='chapter'),1)
    start_href=f'chapter{start_index:05}.xhtml'
    identifier = 'urn:uuid:' + str(uuid.uuid4())
    title, author = book.metadata.title, book.metadata.author
    modified = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    image = cover_bytes(cover_path, title, author, cover_style)
    items = ['<item id="style" href="style.css" media-type="text/css"/>',
             '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
             '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
             '<item id="cover-image" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>',
             '<item id="cover-page" href="cover.xhtml" media-type="application/xhtml+xml"/>']
    spine = ['<itemref idref="cover-page"/>']
    for i in range(1, len(pages) + 1):
        items.append(f'<item id="ch{i}" href="chapter{i:05}.xhtml" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="ch{i}"/>')
    creator = f'<dc:creator id="author">{escape(author)}</dc:creator>' if author else ''
    opf = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="book-id" version="3.0" xml:lang="zh-CN">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="book-id">{identifier}</dc:identifier><dc:title>{escape(title)}</dc:title>{creator}
<dc:language>zh-CN</dc:language><meta property="dcterms:modified">{modified}</meta>
<meta name="cover" content="cover-image"/></metadata>
<manifest>{''.join(items)}</manifest><spine toc="ncx">{''.join(spine)}</spine>
<guide><reference type="cover" title="封面" href="cover.xhtml"/><reference type="text" title="正文" href="{start_href}"/></guide>
</package>'''
    def nav_nodes(nodes):
        return '<ol>' + ''.join('<li><a href="' + n['href'] + '">' + escape(n['title']) + '</a>' +
                (nav_nodes(n['children']) if n['children'] else '') + '</li>' for n in nodes) + '</ol>'
    play_order = 0
    def ncx_nodes(nodes):
        nonlocal play_order
        out = []
        for n in nodes:
            play_order += 1
            num = play_order
            out.append(f'<navPoint id="point{num}" playOrder="{num}"><navLabel><text>{escape(n["title"])}</text></navLabel><content src="{n["href"]}"/>{ncx_nodes(n["children"])}</navPoint>')
        return ''.join(out)
    ncx = f'''<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head>
<meta name="dtb:uid" content="{identifier}"/><meta name="dtb:depth" content="2"/><meta name="dtb:totalPageCount" content="0"/><meta name="dtb:maxPageNumber" content="0"/>
</head><docTitle><text>{escape(title)}</text></docTitle><navMap>{ncx_nodes(toc)}</navMap></ncx>'''
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        z.writestr('META-INF/container.xml', '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr('OEBPS/content.opf', opf)
        css=re.sub(r'(?m)^(body|p) \{[^}]*\}',lambda match:match.group(0).replace('line-height: 1.2;',f'line-height: {float(line_height):g};').replace('margin: 0em auto;',f'margin: {float(paragraph_gap):g}em auto;') if match.group(1)=='p' else match.group(0).replace('line-height: 1.2;',f'line-height: {float(line_height):g};'),CSS)
        z.writestr('OEBPS/style.css', css)
        z.writestr('OEBPS/cover.jpg', image)
        z.writestr('OEBPS/cover.xhtml', xhtml('封面', '<div class="cover"><img src="cover.jpg" alt="'+escape(title, quote=True)+'"/></div>'))
        z.writestr('OEBPS/nav.xhtml', xhtml('目录', '<nav epub:type="toc" id="toc"><h1>目录</h1>'+nav_nodes(toc)+'</nav><nav epub:type="landmarks" hidden="hidden"><ol><li><a epub:type="bodymatter" href="'+start_href+'">正文</a></li></ol></nav>'))
        z.writestr('OEBPS/toc.ncx', ncx)
        for i, chapter in enumerate(pages, 1):
            paragraphs = ''.join('<p>'+escape(line.strip())+'</p>' for line in chapter.text.split('\n') if line.strip())
            z.writestr(f'OEBPS/chapter{i:05}.xhtml', xhtml(chapter.title, '<h1>'+escape(chapter.title)+'</h1>'+paragraphs))
    verify_epub(target)
    return len(pages)

def verify_epub(path):
    with zipfile.ZipFile(path) as z:
        if z.testzip():
            raise ValueError('EPUB 压缩包校验失败')
        first = z.infolist()[0]
        assert first.filename == 'mimetype' and first.compress_type == zipfile.ZIP_STORED
        assert z.read('mimetype') == b'application/epub+zip'
        for name in z.namelist():
            if name.endswith(('.xml', '.opf', '.ncx', '.xhtml')):
                ET.fromstring(z.read(name))
        opf = ET.fromstring(z.read('OEBPS/content.opf'))
        ns = {'o': 'http://www.idpf.org/2007/opf'}
        names = set(z.namelist())
        items = {el.attrib['id']: el.attrib['href'] for el in opf.findall('o:manifest/o:item', ns)}
        for href in items.values():
            assert 'OEBPS/' + href in names, href
        for ref in opf.findall('o:spine/o:itemref', ns):
            assert ref.attrib['idref'] in items
        assert any(el.attrib.get('properties') == 'cover-image' for el in opf.findall('o:manifest/o:item', ns))
