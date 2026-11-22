# SPDX-License-Identifier: AGPL-3.0-only
"""Read novel text and heading hints without Office or external converters."""

from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
import math
import re
import statistics
import struct
import zipfile
import xml.etree.ElementTree as ET
from document_worker import MAX_CHARS, MAX_LINES, MAX_PAGES, read_isolated

SUPPORTED_SUFFIXES = frozenset((".txt", ".docx", ".doc", ".rtf", ".pdf"))
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@dataclass
class DocumentText:
    text: str
    format: str
    headings: dict[int, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    title_hint: str = ""


def source_files(values):
    from batch_limits import _sources

    return sorted(set(_sources(values, None, [])))


def _attr(node, name, default=""):
    return node.get(W + name, default) if node is not None else default


def _enabled(element):
    return element is not None and _attr(element, "val", "true").lower() not in (
        "0",
        "false",
        "off",
    )


def _visible_text(node, visibility, references):
    base, defaults, toggles = visibility
    style = _attr(node.find(W + "pPr/" + W + "pStyle"), "val", defaults.get("paragraph", ""))
    paragraph_hidden = base ^ toggles.get(style, False)
    parts = []

    def walk(item):
        if item.tag in (
            W + "rPr",
            W + "pPr",
            W + "del",
            W + "moveFrom",
            W + "delText",
            W + "instrText",
        ):
            return
        if item.tag == W + "r":
            props = item.find(W + "rPr")
            character = _attr(
                item.find(W + "rPr/" + W + "rStyle"), "val", defaults.get("character", "")
            )
            direct = props.find(W + "vanish") if props is not None else None
            hidden = (
                _enabled(direct)
                if direct is not None
                else paragraph_hidden ^ toggles.get(character, False)
            )
            if hidden:
                return
        if item.tag == W + "t":
            parts.append(item.text or "")
        elif item.tag == W + "tab":
            parts.append("\t")
        elif item.tag in (W + "br", W + "cr"):
            parts.append("\n")
        elif item.tag in (W + "footnoteReference", W + "endnoteReference"):
            note_id = item.get(W + "id", "")
            references.add((item.tag.removeprefix(W).removesuffix("Reference"), note_id))
            parts.append("[注" + note_id + "]")
        else:
            for child in item:
                walk(child)

    walk(node)
    return "".join(parts)


def _word_styles(package):
    levels = {}
    parents = {}
    defaults, direct, toggles = {}, {}, {}
    if "word/styles.xml" not in package.namelist():
        return levels, (False, defaults, toggles)
    root = ET.fromstring(package.read("word/styles.xml"))
    for style in root.findall(W + "style"):
        key = style.get(W + "styleId", "")
        if _attr(style, "default").lower() in ("1", "true", "on"):
            defaults[_attr(style, "type")] = key
        direct[key] = _enabled(style.find(W + "rPr/" + W + "vanish"))
        outline = style.find(W + "pPr/" + W + "outlineLvl")
        name = style.find(W + "name")
        name = name.get(W + "val", "") if name is not None else ""
        if outline is not None:
            try:
                level = int(outline.get(W + "val", "9")) + 1
                if level <= 6:
                    levels[key] = min(level, 2)
            except ValueError:
                pass
        match = re.fullmatch(r"(?:heading|标题)\s*([1-6])", name, re.I)
        if match:
            levels[key] = min(int(match[1]), 2)
        base = style.find(W + "basedOn")
        if base is not None:
            parents[key] = base.get(W + "val", "")
    for _ in range(6):
        for key, parent in parents.items():
            if key not in levels and parent in levels:
                levels[key] = levels[parent]
    for key in direct:
        chain = []
        seen = set()
        current = key
        while current in direct and current not in toggles:
            if current in seen:
                raise ValueError("Word 样式继承循环，请修复文档后导入")
            seen.add(current)
            chain.append(current)
            current = parents.get(current)
        hidden = toggles.get(current, False)
        for item in reversed(chain):
            hidden ^= direct[item]
            toggles[item] = hidden
    base = _enabled(root.find(W + "docDefaults/" + W + "rPrDefault/" + W + "rPr/" + W + "vanish"))
    return levels, (base, defaults, toggles)


def _read_docx(path, limit):
    lines = []
    headings = {}
    warnings = []
    title = ""
    try:
        with zipfile.ZipFile(path) as package:
            members = package.infolist()
            if len(members) > 10000:
                raise ValueError("Word 文件条目过多，请拆分文档后导入")
            limits = {
                "word/document.xml": 96_000_000,
                "word/styles.xml": 4_000_000,
                "word/footnotes.xml": 16_000_000,
                "word/endnotes.xml": 16_000_000,
            }
            relevant = [item for item in members if item.filename in limits]
            if len({item.filename for item in relevant}) != len(relevant):
                raise ValueError("Word 文件包含重复正文或样式条目")
            if sum(item.file_size for item in relevant) > 128_000_000 or any(
                item.file_size > limits[item.filename] for item in relevant
            ):
                raise ValueError("Word 正文、样式或注释解压后过大，请拆分或简化文档后导入")
            info = package.getinfo("word/document.xml")
            levels, visibility = _word_styles(package)
            references = set()
            length = 0
            skipped_toc = False
            with package.open(info) as stream:
                for _, paragraph in ET.iterparse(stream, events=("end",)):
                    if paragraph.tag != W + "p":
                        continue
                    style = paragraph.find(W + "pPr/" + W + "pStyle")
                    style = style.get(W + "val", "") if style is not None else ""
                    content = _visible_text(paragraph, visibility, references).replace("\r", "\n")
                    if re.fullmatch(r"(?:TOC|目录)\s*(?:[1-9]|Heading)", style, re.I):
                        skipped_toc = True
                        paragraph.clear()
                        continue
                    if style.casefold() in ("title", "标题") and content.strip() and not title:
                        title = content.strip()
                    level = levels.get(style)
                    outline = paragraph.find(W + "pPr/" + W + "outlineLvl")
                    if outline is not None:
                        try:
                            value = int(outline.get(W + "val", "9"))
                            if value < 6:
                                level = min(value + 1, 2)
                        except ValueError:
                            pass
                    if content.strip():
                        if level:
                            headings[len(lines) + 1] = level
                        lines.extend(content.split("\n"))
                        lines.append("")
                        length += len(content)
                    paragraph.clear()
                    if limit and length >= limit:
                        break
            if not limit:
                notes = []
                for part, tag in [
                    ("word/footnotes.xml", "footnote"),
                    ("word/endnotes.xml", "endnote"),
                ]:
                    if part not in package.namelist():
                        continue
                    root = ET.fromstring(package.read(part))
                    for note in root.findall(W + tag):
                        note_id = note.get(W + "id", "")
                        if (
                            (tag, note_id) not in references
                            or not note_id.isdigit()
                            or int(note_id) < 1
                        ):
                            continue
                        body = "\n".join(
                            _visible_text(p, visibility, references) for p in note.findall(W + "p")
                        ).strip()
                        if body:
                            notes.append("[注" + note_id + "] " + body)
                if notes:
                    headings[len(lines) + 1] = 1
                    lines.extend(["注释", ""] + notes)
                    warnings.append("脚注与尾注集中放在文末，请预览核对")
            if skipped_toc:
                warnings.append("已忽略 Word 自动目录，电子书目录按正文标题生成")
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise ValueError("无法读取 DOCX；请确认文件未加密且是有效的 Word 文档") from exc
    text = "\n".join(lines).strip()
    if not text and not limit:
        raise ValueError("Word 文档没有可提取的正文文字")
    if not title:
        match = re.search(r"(?m)^\s*《([^》\n]{1,100})》\s*$", text[:4000])
        if match:
            title = match[1]
    return DocumentText(text, "Word DOCX", headings, warnings, title.strip("《》"))


def _fields_to_text(text):
    result = []
    fields = []
    for character in text:
        if character == "\x13":
            fields.append(False)
        elif character == "\x14":
            if fields:
                fields[-1] = True
        elif character == "\x15":
            if fields:
                fields.pop()
        elif all(fields):
            result.append(character)
    return "".join(result)


def _read_doc(path, limit):
    import olefile

    with path.open("rb") as stream:
        signature = stream.read(16)
    if signature.lstrip().startswith(b"{\\rtf"):
        return read_isolated(path, "rtf", limit)
    if not olefile.isOleFile(str(path)):
        raise ValueError("DOC 不是有效的 Word 二进制文档；可另存为 DOCX 后导入")
    with olefile.OleFileIO(str(path)) as package:
        if package.exists("EncryptedPackage"):
            raise ValueError("Word 文档已加密，请先使用密码打开并另存为未加密文件")
        if not package.exists("WordDocument"):
            raise ValueError("文件不包含 Word 正文")
        document = package.openstream("WordDocument").read()
        if len(document) < 154 or struct.unpack_from("<H", document, 0)[0] != 0xA5EC:
            raise ValueError("不支持该 DOC 版本，请另存为 DOCX")
        flags = struct.unpack_from("<H", document, 10)[0]
        if flags & 0x0100:
            raise ValueError("Word 文档已加密，请先解密后导入")
        table_name = "1Table" if flags & 0x0200 else "0Table"
        if not package.exists(table_name):
            raise ValueError("旧版 DOC 缺少文字映射，请另存为 DOCX")
        csw = struct.unpack_from("<H", document, 32)[0]
        lw_count_offset = 34 + csw * 2
        if lw_count_offset + 2 > len(document):
            raise ValueError("DOC 文件信息不完整")
        cslw = struct.unpack_from("<H", document, lw_count_offset)[0]
        lw_start = lw_count_offset + 2
        pairs_count_offset = lw_start + cslw * 4
        if cslw < 4 or pairs_count_offset + 2 > len(document):
            raise ValueError("不支持该 DOC 版本，请另存为 DOCX")
        count = struct.unpack_from("<H", document, pairs_count_offset)[0]
        if count <= 33:
            raise ValueError("DOC 文字映射不可用，请另存为 DOCX")
        ccp_text = struct.unpack_from("<I", document, lw_start + 12)[0]
        offset = pairs_count_offset + 2 + 33 * 8
        if offset + 8 > len(document):
            raise ValueError("DOC 文字映射损坏")
        fc_clx, lcb_clx = struct.unpack_from("<II", document, offset)
        table = package.openstream(table_name).read()
        if fc_clx + lcb_clx > len(table):
            raise ValueError("DOC 文字映射超出文件范围")
        clx = table[fc_clx : fc_clx + lcb_clx]
        cursor = 0
        while cursor < len(clx) and clx[cursor] == 1:
            if cursor + 3 > len(clx):
                raise ValueError("DOC 文字属性损坏")
            cursor += 3 + struct.unpack_from("<H", clx, cursor + 1)[0]
        if cursor + 5 > len(clx) or clx[cursor] != 2:
            raise ValueError("DOC 未找到正文映射")
        length = struct.unpack_from("<I", clx, cursor + 1)[0]
        pieces = clx[cursor + 5 : cursor + 5 + length]
        if len(pieces) != length or length < 4 or (length - 4) % 12:
            raise ValueError("DOC 正文映射损坏")
        count = (length - 4) // 12
        positions = struct.unpack_from("<" + "I" * (count + 1), pieces)
        maximum = min(ccp_text, limit) if limit else ccp_text
        parts = []
        for index in range(count):
            start, end = positions[index], min(positions[index + 1], maximum)
            if start >= maximum:
                break
            if end < start:
                raise ValueError("DOC 正文顺序损坏")
            fc = struct.unpack_from("<I", pieces, 4 * (count + 1) + index * 8 + 2)[0]
            compressed = bool(fc & 0x40000000)
            address = fc & 0x3FFFFFFF
            if compressed:
                address //= 2
            size = (end - start) * (1 if compressed else 2)
            raw = document[address : address + size]
            if len(raw) != size:
                raise ValueError("DOC 正文片段不完整")
            parts.append(raw.decode("cp1252" if compressed else "utf-16le", errors="strict"))
        text = (
            _fields_to_text("".join(parts))
            .replace("\r", "\n")
            .replace("\x07", "\t")
            .replace("\x0b", "\n")
            .replace("\x0c", "\n")
        )
        text = "".join(char for char in text if ord(char) >= 32 or char in "\n\t").strip()
        if not text and not limit:
            raise ValueError("DOC 文档没有可提取的正文文字")
        return DocumentText(
            text, "Word DOC", warnings=["旧版 DOC 按正文提取，复杂排版及修订内容请预览核对"]
        )


def _read_rtf(path, limit):
    from striprtf.striprtf import rtf_to_text

    text = rtf_to_text(path.read_bytes().decode("latin1"), errors="strict").replace("\r", "\n")
    if len(text) > MAX_CHARS:
        raise ValueError("RTF 展开正文超过 400 万字，请拆分后导入")
    if limit:
        text = text[:limit]
    if not text.strip() and not limit:
        raise ValueError("RTF 文档没有可提取的正文文字")
    return DocumentText(text.strip(), "Word RTF")


def _join_lines(first, second):
    if not first:
        return second
    spacer = (
        " "
        if first[-1:].isascii()
        and second[:1].isascii()
        and first[-1:].isalnum()
        and second[:1].isalnum()
        else ""
    )
    return first + spacer + second


def _ordered_blocks(blocks, width):
    middle = width / 2
    left = [
        block
        for block in blocks
        if block["bbox"][2] <= middle + 8
        and sum(len(line.get("text", "")) for line in block["lines"]) > 20
    ]
    right = [
        block
        for block in blocks
        if block["bbox"][0] >= middle - 8
        and sum(len(line.get("text", "")) for line in block["lines"]) > 20
    ]
    if not left or not right:
        return sorted(blocks, key=lambda b: (round(b["bbox"][1], 1), b["bbox"][0])), False
    wide = sorted(
        [b for b in blocks if b["bbox"][0] < middle - 8 and b["bbox"][2] > middle + 8],
        key=lambda b: b["bbox"][1],
    )
    remaining = [b for b in blocks if b not in wide]
    result = []
    for spanning in wide + [None]:
        band = [b for b in remaining if spanning is None or b["bbox"][1] < spanning["bbox"][1]]
        result.extend(
            sorted(
                band, key=lambda b: (0 if b["bbox"][0] < middle else 1, b["bbox"][1], b["bbox"][0])
            )
        )
        remaining = [b for b in remaining if b not in band]
        if spanning is not None:
            result.append(spanning)
    return result, True


def _read_pdf(path, limit):
    import pymupdf

    pages = []
    margin_counts = Counter()
    warnings = []
    title = ""
    scan_pages = []
    toc = {}
    try:
        with pymupdf.open(str(path)) as document:
            if document.needs_pass:
                raise ValueError("PDF 已加密，请先使用密码解密后导入")
            if not document.permissions & pymupdf.PDF_PERM_COPY:
                raise ValueError("PDF 限制文字复制，无法提取正文")
            if len(document) > MAX_PAGES:
                raise ValueError("PDF 超过 4000 页，请拆分后导入")
            characters = line_count = 0
            for level, label, page in document.get_toc():
                toc[label.strip()] = min(level, 2)
            title = (document.metadata or {}).get("title", "").strip()
            if re.fullmatch(
                r"(?:Microsoft Word\s*-\s*)?(?:Document\d*|文档\d*|新建.*)", title, re.I
            ):
                title = ""
            total = min(len(document), 3) if limit else len(document)
            for index in range(total):
                page = document[index]
                raw = page.get_text(
                    "dict", sort=True, flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
                )
                blocks = []
                sizes = []
                margins = set()
                for block in raw.get("blocks", []):
                    if block.get("type") != 0:
                        continue
                    clean_lines = []
                    for line in block.get("lines", []):
                        text = "".join(
                            span.get("text", "") for span in line.get("spans", [])
                        ).strip()
                        if not text:
                            continue
                        characters += len(text)
                        line_count += 1
                        if characters > MAX_CHARS or line_count > MAX_LINES:
                            raise ValueError("PDF 展开后的文字或排版行数过多，请拆分后导入")
                        size = max(
                            (span.get("size", 0) for span in line.get("spans", [])), default=0
                        )
                        sizes.extend([size] * min(len(text), 40))
                        box = line["bbox"]
                        margin = (
                            box[1] < page.rect.height * 0.065 or box[3] > page.rect.height * 0.94
                        )
                        signature = re.sub(r"\d+", "#", re.sub(r"\s+", "", text)) if margin else ""
                        clean_lines.append(dict(text=text, size=size, bbox=box, margin=signature))
                        if signature:
                            margins.add(signature)
                    if clean_lines:
                        blocks.append(dict(bbox=block["bbox"], lines=clean_lines))
                if not blocks and page.get_images():
                    if index == 0:
                        warnings.append(
                            "PDF 第 1 页只有图片，已跳过该页；如包含正文，请先 OCR 后重新导入。图片封面需单独选择。"
                        )
                    else:
                        scan_pages.append(index + 1)
                margin_counts.update(margins)
                pages.append((blocks, page.rect.width, statistics.median(sizes) if sizes else 12))
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("PDF 无法读取，文件可能损坏或格式不受支持") from exc
    if scan_pages and not limit:
        raise ValueError(
            "PDF 第 "
            + "、".join(map(str, scan_pages[:8]))
            + " 页只有图片，请先 OCR 后转换，避免丢失正文"
        )
    threshold = max(2, math.ceil(len(pages) * 0.6))
    repeated = {key for key, count in margin_counts.items() if count >= threshold}
    lines = []
    headings = {}
    removed = 0
    multicolumn = False
    length = 0
    for blocks, width, body_size in pages:
        blocks, column = _ordered_blocks(blocks, width)
        multicolumn |= column
        for block in blocks:
            paragraph = ""
            previous = None
            origin = min(line["bbox"][0] for line in block["lines"])
            for item in block["lines"]:
                text = item["text"]
                if item["margin"] in repeated and item["size"] <= body_size * 1.1:
                    removed += 1
                    continue
                level = toc.get(text)
                if (
                    not level
                    and item["size"] >= body_size * 1.28
                    and 1 < len(text) <= 55
                    and not text.isdigit()
                    and text.strip("《》") != title
                ):
                    level = 1
                gap = previous and item["bbox"][1] - previous["bbox"][3] > body_size * 0.45
                indent = previous and item["bbox"][0] - origin > body_size * 1.3
                if level or gap or indent:
                    if paragraph:
                        lines.extend([paragraph, ""])
                        length += len(paragraph)
                        paragraph = ""
                if level:
                    headings[len(lines) + 1] = level
                    lines.extend([text, ""])
                    length += len(text)
                    previous = None
                else:
                    paragraph = _join_lines(paragraph, text)
                    previous = item
            if paragraph:
                lines.extend([paragraph, ""])
                length += len(paragraph)
            if limit and length >= limit:
                break
        if limit and length >= limit:
            break
    text = "\n".join(lines).strip()
    if limit:
        text = text[:limit]
        line_total = text.count("\n") + 1
        headings = {line: level for line, level in headings.items() if line <= line_total}
    if not text and not limit:
        raise ValueError("PDF 没有可提取的文字，可能是扫描版；请先 OCR 后导入")
    if removed:
        warnings.append(f"已去除 {removed} 处重复页眉页脚或页码")
    if multicolumn:
        warnings.append("检测到双栏文字，已按栏重排，请预览阅读顺序")
    warnings.append("PDF 按文字重新排版，原页码和图文位置不作为电子书版式")
    return DocumentText(text, "PDF 文字", headings, warnings, title)


def read_document(path, limit=None):
    path = Path(path)
    if not path.is_file():
        raise ValueError("源文件不存在")
    from batch_limits import MAX_FILE_BYTES

    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("文件超过单本 64 MiB 限制，请拆分后导入")
    if not path.stat().st_size:
        raise ValueError("源文件为空")
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _read_docx(path, limit)
    if suffix == ".doc":
        return _read_doc(path, limit)
    if suffix in (".rtf", ".pdf"):
        return read_isolated(path, suffix[1:], limit)
    raise ValueError("请选择 TXT、Word（DOC/DOCX/RTF）或文字型 PDF")
