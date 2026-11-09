# SPDX-License-Identifier: AGPL-3.0-only
"""Command-line interface for reproducible batches and future integrations."""

import argparse
from pathlib import Path
import sys
from core import new_job
from batch_limits import collect_sources, import_summary
from batch import Options, run_batch


def main():
    parser = argparse.ArgumentParser(
        description="ZBook 小说转换器：TXT / Word / PDF 批量转换为 EPUB / MOBI"
    )
    parser.add_argument("inputs", nargs="+", help="TXT、Word、PDF 文件或文件夹")
    parser.add_argument("--output", required=True)
    parser.add_argument("--formats", nargs="+", choices=["epub", "mobi"], default=["mobi"])
    parser.add_argument(
        "--auto-cover", action="store_true", help="无本地封面时采用联网首个候选，需自行核对作者"
    )
    parser.add_argument("--converter", default="")
    parser.add_argument("--cover", default="", help="所有输入共用指定封面，仅在明确需要时使用")
    parser.add_argument("--title", default=None, help="仅允许一个输入文件时指定书名")
    parser.add_argument("--author", default=None, help="仅允许一个输入文件时指定作者")
    args = parser.parse_args()
    batch = collect_sources(args.inputs)
    paths = batch.paths
    if batch.rejected:
        parser.error("一次最多导入 50 个文件，本次未导入，请拆分文件夹或减少文件数量")
    if batch.byte_limited or batch.oversized:
        print(import_summary(len(paths), batch), flush=True)
    if (args.title is not None or args.author is not None) and len(paths) != 1:
        parser.error("书名或作者覆盖值只能用于单个输入文件")
    jobs = []
    for p in paths:
        job = new_job(p)
        if args.title is not None:
            job.title = args.title
        if args.author is not None:
            job.author = args.author
        if args.cover:
            job.cover_path = str(Path(args.cover).resolve())
            job.cover_source = "命令行指定封面"
        jobs.append(job)
    result = run_batch(
        jobs,
        Options(args.output, tuple(args.formats), args.converter, args.auto_cover),
        lambda *e: print(*e, flush=True),
    )
    print("报告：", result["report_path"])
    return 0 if result["results"] and all(x["status"] == "完成" for x in result["results"]) else 1


if __name__ == "__main__":
    sys.exit(main())
