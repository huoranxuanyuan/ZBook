# SPDX-License-Identifier: AGPL-3.0-only
"""Modern color covers with strong typography that survives grayscale displays."""
from functools import lru_cache
from io import BytesIO
import re
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1000, 1500
PALETTES = {
    'modern': {'paper': '#F7F5EF', 'text': '#1D2E2C', 'accent': '#347B76',
               'author': '#475953', 'shape': '#B9D3C9', 'second': '#E7DED0',
               'highlight': '#CC8262', 'line': '#6B8F82', 'rule': '#CBD1C8'},
    'warm': {'paper': '#FAF5EC', 'text': '#332B29', 'accent': '#B4674B',
             'author': '#61504A', 'shape': '#E4BD9F', 'second': '#D7DED1',
             'highlight': '#507A73', 'line': '#A17157', 'rule': '#D4C8BB'},
}


def normalize_cover_style(style):
    # Migrate both old ink/color preferences to the new color design.
    return style if style in PALETTES else 'modern'


@lru_cache(maxsize=1)
def _font_data():
    import pymupdf
    return pymupdf.Font('cjk').buffer


@lru_cache(maxsize=32)
def font(size):
    return ImageFont.truetype(BytesIO(_font_data()), int(size))


def _clean(value):
    return re.sub(r'\s+', ' ', re.sub(r'[\x00-\x08\x0b-\x1f]', '', str(value or ''))).strip()


def _wrap(text, face, width, limit):
    lines = []; current = ''; current_width = 0.; clipped = False; widths = {}
    for character in text:
        if character not in widths:
            widths[character] = face.getlength(character)
        step = widths[character]
        if current and current_width + step > width:
            lines.append(current.rstrip()); current = ''; current_width = 0
            if len(lines) == limit:
                clipped = True; break
        current += character; current_width += step
    if current and len(lines) < limit:
        lines.append(current.rstrip())
    if clipped:
        last = lines[-1]
        while last and face.getlength(last + '…') > width:
            last = last[:-1]
        lines[-1] = last + '…'
    return lines or [''], clipped


def text_layout(title, author):
    title = _clean(title) or '未命名作品'
    for size in (176, 164, 152, 140, 128, 116, 104, 92, 80, 68, 56, 44, 36, 30):
        face = font(size); lines, clipped = _wrap(title, face, 808, 9); step = round(size * 1.22)
        if not clipped and len(lines) * step <= 690:
            break
    if len(lines) * step > 690:
        lines, _ = _wrap(title, face, 808, min(9, max(1, 690 // step)))
    top = max(270, 610 - len(lines) * step // 2)
    author_lines, _ = _wrap(_clean(author), font(48), 800, 2)
    return {'title': lines, 'size': size, 'author': author_lines,
            'title_top': top, 'line_step': step}


def _artwork(draw, colors):
    # Separate silhouettes and lightness levels also work on monochrome e-ink.
    draw.rectangle((0, 1190, WIDTH, HEIGHT), fill=colors['second'])
    draw.ellipse((420, 1080, 1120, 1780), fill=colors['shape'])
    draw.ellipse((710, 1110, 850, 1250), fill=colors['highlight'])
    draw.rounded_rectangle((96, 1160, 362, 1660), radius=133, fill=colors['accent'])
    draw.rounded_rectangle((159, 1223, 299, 1650), radius=70, fill=colors['paper'])
    for index in range(4):
        inset = index * 26
        draw.arc((476 + inset, 1150 + inset, 1064 - inset, 1738 - inset),
                 185, 355, fill=colors['line'], width=3)
    draw.line((88, 1430, 912, 1430), fill=colors['rule'], width=2)


@lru_cache(maxsize=80)
def generated_cover(title, author, placeholder=False, style='modern'):
    colors = PALETTES[normalize_cover_style(style)]
    image = Image.new('RGB', (WIDTH, HEIGHT), colors['paper'])
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 23, HEIGHT), fill=colors['accent'])
    draw.rounded_rectangle((88, 92, 160, 164), radius=19, fill=colors['accent'])
    draw.line([(108, 113), (140, 113), (108, 143), (140, 143)],
              fill='#FFFFFF', width=5, joint='curve')
    draw.text((184, 109), 'ZBOOK', font=font(28), fill=colors['author'])
    draw.line((88, 220, 912, 220), fill=colors['rule'], width=2)
    if placeholder:
        lines = ['每一本书', '新的世界']; size = 152; step = 190; top = 380
        author_lines = ['导入作品，开始阅读']
    else:
        layout = text_layout(title, author)
        lines = layout['title']; size = layout['size']; step = layout['line_step']
        top = layout['title_top']; author_lines = layout['author']
    for index, line in enumerate(lines):
        draw.text((88, top + index * step), line, font=font(size), fill=colors['text'],
                  stroke_width=2 if size >= 104 else 1, anchor='lt')
    author_top = top + len(lines) * step + 70
    for index, line in enumerate(author_lines):
        if line:
            draw.text((92, author_top + index * 58), line, font=font(48),
                      fill=colors['author'], anchor='lt')
    _artwork(draw, colors)
    output = BytesIO()
    image.save(output, 'JPEG', quality=93, subsampling=0, optimize=True)
    return output.getvalue()
