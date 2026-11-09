# SPDX-License-Identifier: AGPL-3.0-only
"""Text parsing and book metadata. Independent of UI and export backends."""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from pathlib import Path
import codecs
import re
import unicodedata
import uuid

NUM = r"0-9０-９零〇○一二三四五六七八九十百千万亿两壹贰叁肆伍陆柒捌玖拾佰仟"
NUMBERED = re.compile(rf"^第\s*[{NUM}]+\s*([章回节卷集部篇])(?:\s*.*)?$")
CHAPTER_MARKER = re.compile(rf"第\s*([{NUM}]+)\s*([章回节])")
ENGLISH = re.compile(r"^(chapter|part|volume|book)\s+(?:\d+|[IVXLCDM]+)\b.*$", re.I)
SPECIAL = re.compile(
    r"^(?:序章|序言|前言|楔子|引子|内容简介|作品简介|后记|尾声|终章|番外|附录|完本感言|完结感言)(?:\s|[：:·、（(\d一二三四五六七八九十]|$).*"
)
EDITION = re.compile(
    r"(?:校对|精校|读校|全本|完本|完结|完整版|无删减|全番外|无忧|整理|修订|全卷|全集|TXT|txt)"
)


@dataclass
class Metadata:
    title: str
    author: str = ""
    source: str = "文件名"
    confidence: str = "明确"
    notes: list[str] = field(default_factory=list)


@dataclass
class Chapter:
    title: str
    text: str
    line: int
    level: int = 1
    kind: str = "chapter"


@dataclass
class ParsedBook:
    metadata: Metadata
    chapters: list[Chapter]
    encoding: str
    characters: int
    warnings: list[str] = field(default_factory=list)


def count_chapters(book):
    """Count reading chapters, excluding volume nodes and implicit front matter."""
    count = sum(chapter.kind == "chapter" for chapter in book.chapters)
    if count:
        return count
    return int(
        bool(book.chapters) and not any(chapter.kind == "volume" for chapter in book.chapters)
    )


@dataclass
class BookJob:
    path: str
    title: str
    author: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    selected: bool = True
    encoding: str = "自动"
    rule: str = "自动（中文 / 英文）"
    custom_regex: str = ""
    cover_path: str = ""
    cover_source: str = ""
    cover_mode: str = "auto"
    status: str = "待转换"
    notes: list[str] = field(default_factory=list)
    chapter_count: int = 0
    chapter_titles: dict[str, str] = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        keys = cls.__dataclass_fields__
        return cls(**{k: v for k, v in data.items() if k in keys})


def _clean_edition(value):
    def replace(m):
        return "" if EDITION.search(m.group(0)) else m.group(0)

    return re.sub(r"[（(【\[].*?[）)】\]]", replace, value).strip(" _-\t")


def parse_metadata(filename, header=""):
    stem = Path(filename).stem.strip()
    notes = []
    if stem.startswith("句号-"):
        stem = stem[3:]
        notes.append("移除了已知整理前缀“句号-”")
    explicit = re.search(r"(?:作者|作\s*者)\s*[:：]\s*(.+)$", stem)
    author = ""
    body = stem
    if explicit:
        author = _clean_edition(explicit.group(1)).strip(" 《》")
        body = stem[: explicit.start()].rstrip()
    bracket = re.search(r"《([^》]+)》", body)
    if bracket:
        title = bracket.group(1).strip()
        remainder = _clean_edition(body[bracket.end() :])
    else:
        # Genre and author bracket style: 〔仙侠〕书名〔作者〕.
        body = re.sub(
            r"^〔(?:仙侠|玄幻|都市|科幻|奇幻|历史|武侠|游戏|言情|轻小说|悬疑)〕", "", body
        )
        tail = re.search(r"〔([^〕]+)〕$", body)
        if tail and not author:
            author = tail.group(1).strip()
            body = body[: tail.start()]
        title = _clean_edition(body)
        remainder = ""
        # Only split visibly separated names; bare hyphens may belong to a title.
        sep = re.fullmatch(r"(.+?)\s+(?:-|—|–)\s+([^\s]{1,30})", title)
        if sep and not author:
            title, author = sep.groups()
            notes.append("按“书名 - 作者”识别，请核对")
    confidence = "明确"
    if not author and remainder and len(remainder) <= 24:
        author = remainder
        confidence = "待核对"
        notes.append("将书名后、版本标记外的文字识别为作者，请核对")
    if not bracket and not explicit and not author:
        # e.g. 暴风城打工实录（完结全本）又一个鱼雷
        m = re.fullmatch(r"(.+?)[（(]([^）)]+)[）)]\s*([^\s（()）]{1,24})", stem)
        if m and EDITION.search(m.group(2)):
            title, author = m.group(1).strip(), m.group(3).strip()
            confidence = "待核对"
            notes.append("按版本标记后的尾部识别作者，请核对")
    title = re.sub(r"(?:精校板|精校版|校对版|精校全本|校对全本)$", "", title).strip()
    header_area = re.split(rf"(?m)^[ \t\u3000]*第\s*[{NUM}]+\s*[章回卷集]", header, maxsplit=1)[0][
        :8000
    ]
    hm = re.search(
        r"(?m)(?:^|[ \t\u3000])(?:作者|作[ \t]*者)[ \t]*[:：][ \t]*([^\r\n]{1,100})", header_area
    )
    header_author = ""
    if hm:
        header_author = re.split(
            r"[,，;；|][ \t]*(?:字数|状态|类型|类别|简介|来源|网址)[ \t]*[:：]", hm.group(1)
        )[0].strip()
        header_author = re.sub(r"[ \t]+著$", "", header_author)
    from_header = False
    if not author and hm:
        author = header_author
        from_header = True
        notes.append("作者来自正文开头")
    if not author:
        author_line = re.search(
            r"(?m)^[ \t\u3000]*(?:《"
            + re.escape(title)
            + r"》|"
            + re.escape(title)
            + r")[ \t]+([^\r\n]{1,30})[ \t]+著[ \t]*$",
            header_area,
        )
        if author_line:
            author = author_line.group(1).strip()
            from_header = True
            notes.append("作者来自正文“书名 作者 著”")
    if not author:
        lines = [line.strip() for line in header_area.splitlines() if line.strip()]
        for i, line in enumerate(lines[:-2]):
            if (
                line.strip("《》") == title
                and len(lines[i + 1]) <= 24
                and re.fullmatch(r"[\d.]+[万千亿]?字", lines[i + 2])
            ):
                author = lines[i + 1]
                confidence = "待核对"
                from_header = True
                notes.append("从书名与字数之间的独立行识别作者，请核对")
                break
    if not author:
        confidence = "待核对"
        notes.append("未识别作者，保留为空")
    if not title:
        title = Path(filename).stem
        confidence = "待核对"
    if (
        explicit
        and hm
        and unicodedata.normalize("NFKC", author) != unicodedata.normalize("NFKC", header_author)
    ):
        notes.append("文件名作者与正文作者不同，保留文件名值，请核对")
        confidence = "待核对"
    return Metadata(
        title.strip(),
        author.strip(),
        "文件名 / 正文头部" if from_header else "文件名",
        confidence,
        notes,
    )


def decode_bytes(data, encoding="自动"):
    if encoding != "自动":
        return data.decode(encoding, errors="strict"), encoding
    if data.startswith(codecs.BOM_UTF8):
        return data.decode("utf-8-sig"), "utf-8-sig"
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16"), "utf-16"
    if b"\0" in data[:1000]:
        for enc in ("utf-16le", "utf-16be"):
            try:
                text = data.decode(enc)
                if re.search(rf"第[{NUM}]+章", text[:30000]):
                    return text, enc
            except UnicodeError:
                pass
        raise ValueError("检测到空字节，无法可靠识别编码；请手动选择 UTF-16LE 或 UTF-16BE")
    for enc in ("utf-8", "gb18030"):
        try:
            return data.decode(enc), enc
        except UnicodeError:
            pass
    from charset_normalizer import from_bytes

    detected = from_bytes(data).best()
    if detected and detected.encoding and detected.chaos < 0.1:
        return str(detected), detected.encoding
    raise ValueError("无法可靠识别文本编码，请在右侧手动选择；没有用替换字符吞掉原文")


VOLUME_TOKEN = rf"(?:第\s*[{NUM}]+\s*(?:部分|卷|集|部|篇)|卷\s*[{NUM}]+|[上中下序终]卷)"
VOLUME_PREFIX = re.compile(rf"^(?P<marker>{VOLUME_TOKEN})(?P<tail>.*)$")
VOLUME_ENGLISH = re.compile(
    r"^(?P<marker>(?P<unit>part|volume|book)\s+(?:\d+|[IVXLCDM]+))(?P<tail>.*)$", re.I
)


def volume_heading(text):
    """Recognize title-shaped volume markers, never arbitrary words like 部族."""
    value = re.sub(r"^正文[\s：:]*", "", text.strip("\ufeff \t\u3000"))
    if not value or len(value) > 70:
        return None
    match = VOLUME_PREFIX.fullmatch(value) or VOLUME_ENGLISH.fullmatch(value)
    if not match:
        return None
    marker, raw_tail = match["marker"], match["tail"]
    # A separator (or an immediately attached chapter marker) is required.
    # 第一部族、第二部分是、第二卷就写 are prose, regardless of punctuation.
    if (
        raw_tail
        and raw_tail[0] not in " \t\u3000：:、·.-（(【["
        and not CHAPTER_MARKER.match(raw_tail)
    ):
        return None
    tail = raw_tail.strip(" \t\u3000：:、·.-")
    chapter = CHAPTER_MARKER.search(tail)
    if chapter:
        chapter_text = tail[chapter.start() :].strip()
        if (
            "。" in chapter_text.rstrip("。")
            or chapter_text.count("”") > chapter_text.count("“")
            or chapter_text.count("」") > chapter_text.count("「")
        ):
            return None
    middle = tail[: chapter.start()].strip(" \t\u3000：:、·.-") if chapter else tail
    if len(middle) > 48 or re.search(r"[。；;“”「」]|\.{3}|…", middle):
        return None
    note = middle.strip("（）()【】[] \t")
    if re.fullmatch(r"(?:完|终|结束|完结|卷终|本卷完|本卷结束|全文完)", note):
        return None
    if re.search(r"创作后期|创作总结|本卷总结|修改说明|修订说明|完结感言|更新通知", middle):
        return None
    chinese = re.search(r"(部分|卷|集|部|篇)$", marker)
    family = chinese[1] if chinese else match.groupdict().get("unit", "卷").casefold()
    return {
        "marker": marker,
        "family": family,
        "title": (marker + " " + middle).strip(),
        "chapter": tail[chapter.start() :].strip() if chapter else None,
    }


def heading_kind(text, rule, custom=None):
    s = text.strip("\ufeff \t\u3000")
    s = re.sub(r"^正文[\s：:]*", "", s)
    if not s or len(s) > 70:
        return None
    if custom is not None:
        if heading_kind(s, "自动（中文 / 英文）", None) == "volume":
            return "volume"
        return "chapter" if custom.fullmatch(s) else None
    volume = volume_heading(s)
    if volume:
        return "chapter" if volume["chapter"] else "volume"
    match = NUMBERED.fullmatch(s)
    if match:
        if match.group(1) in ("卷", "集", "部", "篇"):
            return None
        if "。" in s.rstrip("。"):
            return None
        return "chapter"
    if "。" in s:
        return None
    match = ENGLISH.fullmatch(s)
    if match:
        return "chapter" if match.group(1).lower() == "chapter" else None
    if SPECIAL.fullmatch(s):
        return "intro" if re.match(r"^(?:内容简介|作品简介|前言|序言)", s) else "chapter"
    if rule == "数字标题" and re.fullmatch(r"\d{1,5}(?:[.、：:]\s*|\s+).{1,55}", s):
        return "chapter"
    return None


def volume_boundaries(lines, kinds, heading_levels=None):
    """Require chapter context, an explicit document heading, or an isolated series."""
    candidates = {
        index: volume_heading(line) for index, line in enumerate(lines) if kinds[index] == "volume"
    }
    families = {}
    for info in candidates.values():
        if info:
            families.setdefault(info["family"], set()).add(re.sub(r"\s+", "", info["marker"]))
    accepted = set()
    for index, info in candidates.items():
        if not info:
            continue
        if (heading_levels or {}).get(index + 1):
            accepted.add(index)
            continue
        nonempty = 0
        characters = 0
        next_chapter = False
        for other in range(index + 1, len(lines)):
            if not lines[other].strip():
                continue
            nonempty += 1
            characters += len(lines[other])
            if nonempty > 12 or characters > 2400:
                break
            if kinds[other] == "chapter":
                next_chapter = True
                break
            if kinds[other] == "volume":
                break
        before_blank = index == 0 or not lines[index - 1].strip()
        after_blank = index + 1 == len(lines) or not lines[index + 1].strip()
        unindented = lines[index] == lines[index].lstrip("\ufeff \t\u3000")
        isolated = before_blank and (after_blank or unindented)
        if next_chapter or isolated and len(families.get(info["family"], ())) >= 2:
            accepted.add(index)
    return accepted


def automatic_volumes(chapters):
    """Separate actual combined volume/chapter headings without fabricating body headings."""

    def key(title):
        info = volume_heading(title)
        return re.sub(r"\s+", "", info["marker"] if info else title).casefold()

    result = []
    active = None
    for chapter in chapters:
        if chapter.kind == "volume":
            active = key(chapter.title)
            chapter.level = 1
        elif chapter.kind == "chapter":
            info = volume_heading(chapter.title)
            if info and info["chapter"]:
                found = key(info["title"])
                if active != found:
                    result.append(Chapter(info["title"], "", -chapter.line, 1, "volume"))
                    active = found
                chapter.title = info["chapter"]
            if active is not None:
                chapter.level = 2
        elif active is not None:
            chapter.level = 2
        result.append(chapter)
    return result


def parse_text(
    text,
    metadata,
    encoding="utf-8",
    rule="自动（中文 / 英文）",
    custom_regex="",
    heading_levels=None,
):
    # CRCRLF occurs in several real files. Normalize line boundaries, not wording.
    text = text.replace("\r\r\n", "\n").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    custom = None
    if rule == "自定义正则":
        if not custom_regex.strip():
            raise ValueError("请填写自定义章节正则")
        if len(custom_regex) > 300:
            raise ValueError("章节正则过长")
        from safe_regex import HeadingPattern

        custom = HeadingPattern(custom_regex)
    chapters = []
    content = []
    current_title, current_kind, current_line = "前言", "intro", 1
    current_level = 1
    has_volume = False
    lines = text.split("\n")
    kinds = [heading_kind(line, rule, custom) for line in lines]
    accepted_volumes = volume_boundaries(lines, kinds, heading_levels)
    for number, line in enumerate(lines, 1):
        kind = kinds[number - 1]
        rejected_volume = kind == "volume" and number - 1 not in accepted_volumes
        if rejected_volume:
            kind = None
        hinted = (
            (heading_levels or {}).get(number)
            if custom is None and rule.startswith("自动")
            else None
        )
        if line.strip().strip("《》") == metadata.title:
            hinted = None
        if hinted and not rejected_volume:
            kind = kind or "chapter"
        if kind:
            stripped = line.strip()
            current_marker = CHAPTER_MARKER.search(current_title)
            next_marker = CHAPTER_MARKER.search(stripped)
            same_marker = (
                custom is None
                and current_kind == kind == "chapter"
                and current_marker
                and next_marker
                and current_marker.groups() == next_marker.groups()
            )
            next_volume = volume_heading(stripped) if kind == "chapter" else None
            if same_marker and next_volume and next_volume["chapter"]:
                current_volume = volume_heading(current_title)
                if not current_volume or current_volume["marker"] != next_volume["marker"]:
                    same_marker = False
            repeated = (
                stripped.startswith(current_title) if custom is None else stripped == current_title
            )
            if current_kind != "intro" and (
                same_marker or repeated and not any(part.strip() for part in content)
            ):
                content.append(line)
                continue
            if any(part.strip() for part in content) or chapters or current_kind != "intro":
                chapters.append(
                    Chapter(
                        current_title, "\n".join(content), current_line, current_level, current_kind
                    )
                )
            current_title, current_kind, current_line = line.strip(), kind, number
            if kind == "volume":
                has_volume = True
            current_level = hinted or (2 if has_volume and kind == "chapter" else 1)
            content = []
        else:
            content.append(line)
    if content or chapters:
        chapters.append(
            Chapter(current_title, "\n".join(content), current_line, current_level, current_kind)
        )
    if not chapters:
        chapters = [Chapter(metadata.title, text, 1)]
    if len(chapters) == 1 and chapters[0].kind == "intro":
        chapters[0].title = metadata.title
    chapters = automatic_volumes(chapters)
    warnings = []
    count = sum(c.kind == "chapter" for c in chapters)
    if count == 0:
        warnings.append("未识别到章节标题，将完整正文作为一章导出；可切换章节规则")
    if len(chapters) > max(30, len(lines) // 6):
        warnings.append("章节密度偏高，可能把正文误识别为标题，请预览目录")
    if any(len(c.text) > 180000 for c in chapters):
        warnings.append("存在较长章节，导出时会自动分页，保留全部正文")
    return ParsedBook(metadata, chapters, encoding, len(text), warnings)


def document_metadata(path, document):
    metadata = parse_metadata(Path(path).name, document.text[:12000])
    visible_title = re.search(r"(?m)^\s*《([^》\n]{1,100})》\s*$", document.text[:4000])
    hint = document.title_hint or (visible_title[1] if visible_title else "")
    if hint and not re.search(r"《[^》]+》|作者\s*[:：]", Path(path).stem):
        metadata.title = hint
        metadata.source = "文档标题 / 文件名"
        metadata.notes.append("书名来自文档标题")
    return metadata


def read_book(
    path, encoding="自动", rule="自动（中文 / 英文）", custom_regex="", title=None, author=None
):
    path = Path(path)
    document = None
    if path.suffix.lower() == ".txt":
        data = path.read_bytes()
        if not data:
            raise ValueError("源文件为空")
        text, detected = decode_bytes(data, encoding)
        metadata = parse_metadata(path.name, text[:12000])
    else:
        from input_sources import read_document

        document = read_document(path)
        text = document.text
        detected = document.format
        metadata = document_metadata(path, document)
    if title is not None:
        metadata.title = title.strip()
    if author is not None:
        metadata.author = author.strip()
    if not metadata.title:
        raise ValueError("书名不能为空")
    book = parse_text(
        text, metadata, detected, rule, custom_regex, document.headings if document else None
    )
    if document:
        book.warnings = list(dict.fromkeys(document.warnings + book.warnings))
    return book


def new_job(path):
    path = Path(path).resolve()
    if path.suffix.lower() == ".txt":
        with path.open("rb") as f:
            data = f.read(32000)
        header = ""
        choices = (
            ("utf-16",)
            if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE))
            else ("utf-8-sig", "gb18030")
        )
        for enc in choices:
            try:
                header = codecs.getincrementaldecoder(enc)("strict").decode(data, final=False)
                break
            except UnicodeError:
                continue
        meta = parse_metadata(path.name, header)
    else:
        from input_sources import read_document

        meta = document_metadata(path, read_document(path, limit=16000))
    job = BookJob(str(path), meta.title, meta.author, notes=meta.notes)
    for candidate in (
        path.with_suffix(".jpg"),
        path.with_suffix(".png"),
        path.parent / "cover.jpg",
        path.parent / "cover.png",
    ):
        if candidate.is_file():
            job.cover_path = str(candidate)
            job.cover_source = "同目录图片"
            break
    return job


def read_job(job):
    book = read_book(
        job.path,
        job.encoding,
        job.rule,
        job.custom_regex,
        job.title,
        job.author if job.author or Path(job.path).suffix.lower() == ".txt" else None,
    )
    for chapter in book.chapters:
        if chapter.kind != "volume":
            chapter.title = job.chapter_titles.get(str(chapter.line), chapter.title)
    return book


def safe_filename(text):
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip(" .")
    if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", text):
        text = "_" + text
    return text[:100] or "未命名"
