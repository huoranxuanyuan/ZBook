# SPDX-License-Identifier: AGPL-3.0-only
"""Shareable diagnostics contain only program-defined categories and counts."""

from collections import Counter
from pathlib import Path

from branding import VERSION
from input_sources import SUPPORTED_SUFFIXES


def summary(jobs, epub, mobi, auto_cover):
    formats = Counter(Path(job.path).suffix.lower() for job in jobs)
    states = Counter(job.status.partition(" · ")[0] for job in jobs)
    inputs = "、".join(
        f"{ext[1:].upper()} {formats[ext]}" for ext in sorted(SUPPORTED_SUFFIXES) if formats[ext]
    )
    outputs = " + ".join(name for enabled, name in ((epub, "EPUB"), (mobi, "MOBI")) if enabled)
    return "\n".join(
        (
            f"ZBook {VERSION}",
            f"队列 {len(jobs)} 本，已勾选 {sum(bool(job.selected) for job in jobs)} 本",
            f"输入格式 {inputs or '无'}",
            f"输出格式 {outputs or '无'}",
            f"自动搜封面 {'开启' if auto_cover else '关闭'}",
            "转换状态 "
            + "、".join(
                f"{name} {states[name]}" for name in ("完成", "部分完成", "失败", "已停止")
            ),
        )
    )
