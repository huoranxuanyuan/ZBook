# SPDX-License-Identifier: AGPL-3.0-only
"""Optional chapter headings for selected books. Only full lines can match."""

from pathlib import Path
import time

from safe_regex import HeadingPattern

SUPPLEMENT = "自动＋补充规则"
RULES = ("自动（中文 / 英文）", "数字标题", SUPPLEMENT, "自定义正则")
TEMPLATE = """# 每行一条正则，只补充当前选择“自动＋补充规则”的书。
# 以 # 开头的是说明。取消下一行开头的 # 和空格即可启用示例。
# 幕间[ ·].{1,35}
# 保存后，在 ZBook 中重新识别目录，再核对预览。
"""


def rules_path():
    from paths import DATA

    return DATA / "章节补充规则.txt"


def editable_rules():
    path = rules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(TEMPLATE)
    except FileExistsError:
        pass
    return path


def shared_rules(path):
    try:
        with path.open("rb") as stream:
            raw = stream.read(32769)
        if len(raw) > 32768:
            raise ValueError("章节补充规则文件超过 32 KiB")
        return raw.decode("utf-8-sig").splitlines()
    except FileNotFoundError:
        return []
    except (OSError, UnicodeError) as exc:
        raise ValueError("无法读取章节补充规则，请将文件保存为 UTF-8。") from exc


class SupplementalChapters:
    def __init__(self, book_pattern="", path=None, snapshot=None):
        lines = (
            shared_rules(Path(path) if path is not None else rules_path())
            if snapshot is None
            else snapshot
        )
        self.patterns = []
        self.spent = 0.0
        entries = [
            (f"第 {index} 行", line.strip())
            for index, line in enumerate(lines, 1)
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.shared_patterns = [value for _, value in entries]
        if book_pattern.strip():
            entries.append(("本书", book_pattern.strip()))
        if len(entries) > 32:
            raise ValueError("一本书最多使用 32 条补充规则")
        for label, value in entries:
            try:
                self.patterns.append(HeadingPattern(value))
            except ValueError as exc:
                raise ValueError(f"章节补充规则（{label}）无效。{exc}") from exc

    def matches(self, line):
        text = line.strip("\ufeff \t\u3000")
        if not self.patterns or not 0 < len(text) <= 70:
            return False
        if self.spent >= 2:
            raise ValueError("章节补充规则匹配用时过长，请精简规则并重新识别。")
        start = time.monotonic()
        try:
            return any(pattern.fullmatch(text) for pattern in self.patterns)
        finally:
            self.spent += time.monotonic() - start
