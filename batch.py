# SPDX-License-Identifier: AGPL-3.0-only
"""Batch orchestration and external encoding, with non-overwriting publication."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import json
import os
import shutil
import subprocess
import threading
import time
import traceback
import zipfile
from xml.etree import ElementTree as ET
import uuid
from core import BookJob, read_job, safe_filename, count_chapters
from batch_limits import validate_batch
from epub_export import write_epub
from document_worker import document_cancellation

from paths import ROOT
from encoder_trust import expected_encoder, find_converter, verified_encoder
from reading_style import LINE_HEIGHT, PARAGRAPH_GAP
from cache_maintenance import create_maintenance, verify_published_files


@dataclass
class Options:
    output: str = str(ROOT / "output")
    formats: tuple[str, ...] = ("mobi",)
    converter: str = ""
    auto_cover: bool = False
    line_height: float = LINE_HEIGHT
    paragraph_gap: float = PARAGRAPH_GAP
    cover_style: str = "modern"
    explicit_encoder: dict | None = None


def publish(source, output, stem, extension):
    source, output = Path(source), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    staging = output / (".ZBook写入中-" + uuid.uuid4().hex + ".tmp")
    try:
        with source.open("rb") as src, staging.open("xb") as dst:
            shutil.copyfileobj(src, dst)
            dst.flush()
            os.fsync(dst.fileno())
        for number in range(1, 10001):
            target = output / (stem + ("" if number == 1 else f" ({number})") + "." + extension)
            if target.exists():
                continue
            try:
                # Windows rename refuses to replace an existing destination.
                if os.name == "nt":
                    os.rename(staging, target)
                else:
                    os.link(staging, target)
                    shutil.move(str(staging), str(source.parent / staging.name))
                return str(target)
            except FileExistsError:
                continue
        raise ValueError("同名输出文件过多，请换一个输出目录")
    finally:
        if staging.exists():
            shutil.move(str(staging), str(source.parent / staging.name))


def convert_mobi(epub, target, selected, cancel, log_path, on_progress=None):
    converter = selected["path"]
    is_kindlegen = Path(converter).name.lower() == "kindlegen.exe"
    is_kindling = Path(converter).name.lower() in ("kindling.exe", "kindling-cli-windows.exe")
    if is_kindling:
        # Kindling inserts the cover image itself. Remove only the redundant
        # cover spine entry in a separate encoder input; public EPUB is unchanged.
        encoder_input = Path(target).parent / "encoder.epub"
        with zipfile.ZipFile(epub) as original, zipfile.ZipFile(encoder_input, "w") as encoded:
            for info in original.infolist():
                data = original.read(info.filename)
                if info.filename == "OEBPS/content.opf":
                    document = ET.fromstring(data)
                    ns = {"o": "http://www.idpf.org/2007/opf"}
                    spine = document.find("o:spine", ns)
                    if spine is None:
                        raise ValueError("EPUB 缺少正文顺序")
                    for item in list(spine):
                        if item.get("idref") == "cover-page":
                            spine.remove(item)
                    data = ET.tostring(document, encoding="utf-8", xml_declaration=True)
                encoded.writestr(info, data)
        command = [
            converter,
            "build",
            str(encoder_input),
            "-o",
            str(target),
            "--legacy-mobi",
            "--creator-tag",
        ]
    elif is_kindlegen:
        if Path(epub).parent.resolve() != Path(target).parent.resolve():
            raise ValueError("内置编码器需要输入与中间输出处于同一任务目录")
        command = [converter, str(epub), "-o", Path(target).name, "-c1", "-locale", "zh"]
    else:
        command = [
            converter,
            str(epub),
            str(target),
            "--mobi-file-type",
            "both",
            "--output-profile",
            "kindle_pw3",
            "--no-inline-toc",
            "--disable-font-rescaling",
            "--chapter-mark",
            "none",
            "--minimum-line-height",
            "0",
            "--margin-top",
            "-1",
            "--margin-bottom",
            "-1",
            "--margin-left",
            "-1",
            "--margin-right",
            "-1",
        ]
    env = os.environ.copy()
    for key in ["PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH", "QML2_IMPORT_PATH", "VIRTUAL_ENV"]:
        env.pop(key, None)
    with Path(log_path).open("w", encoding="utf8") as log:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        with verified_encoder(selected):
            proc = subprocess.Popen(
                command, stdout=log, stderr=subprocess.STDOUT, creationflags=flags, env=env
            )
        start = time.monotonic()
        last_notice = start
        while proc.poll() is None:
            if on_progress and time.monotonic() - last_notice >= 3:
                on_progress(f"编码 MOBI · 已用时 {int(time.monotonic()-start)} 秒")
                last_notice = time.monotonic()
            if cancel.is_set() or time.monotonic() - start > 1200:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                if cancel.is_set():
                    raise InterruptedError("已停止，未发布未完成文件")
                raise TimeoutError("MOBI 编码超过 20 分钟，已结束该任务")
            time.sleep(0.2)
        accepted = proc.returncode in (0, 1) if is_kindlegen else proc.returncode == 0
        if not accepted or not Path(target).is_file() or Path(target).stat().st_size < 100:
            raise RuntimeError(f"MOBI 编码失败，请查看 {log_path}")
    with Path(target).open("rb") as f:
        header = f.read(80)
    if header[60:68] != b"BOOKMOBI":
        raise ValueError("输出未通过 MOBI 文件头检查")
    return proc.returncode


def _run_batch(jobs, options, emit=None, cancel=None, maintenance=None):
    emit = emit or (lambda *args: None)
    cancel = cancel or threading.Event()
    if not options.formats or set(options.formats) - {"epub", "mobi"}:
        raise ValueError("请选择 EPUB 或 MOBI")
    converter = None
    if "mobi" in options.formats:
        converter = expected_encoder(options.converter, options.explicit_encoder)
        with verified_encoder(converter):
            pass
    output = Path(options.output).resolve()
    selected = [job for job in jobs if job.selected]
    if not selected:
        raise ValueError("请先勾选至少一本书")
    validate_batch(selected)
    # Copy job values in the UI before calling. Workers never mutate live widgets.
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    archive = ROOT / "archive" / "runs" / run_id
    maintenance.protect_resource(archive)
    archive.mkdir(parents=True, exist_ok=True)
    progress_log = archive / "progress.log"
    emit("run_started", str(archive / "report.json"), str(progress_log))
    results = []

    def phase(job, index, stage):
        message = f"[{index}/{len(selected)}] {job.title} · {stage}"
        with progress_log.open("a", encoding="utf-8") as log:
            log.write(time.strftime("%H:%M:%S") + "  " + message + "\n")
        emit("status", job.id, stage)
        emit("log", job.id, message)

    for index, job in enumerate(selected, 1):
        if cancel.is_set():
            break
        emit("batch_current", job.id, index, len(selected))
        work = archive / job.id
        work.mkdir(exist_ok=True)
        result = {
            "id": job.id,
            "input": job.path,
            "title": job.title,
            "author": job.author,
            "outputs": [],
            "status": "处理中",
            "warnings": [],
        }
        phase(job, index, "读取与分章")
        try:
            with document_cancellation(cancel.is_set):
                book = read_job(job, use_preview_rules=True)
            result["encoding"] = book.encoding
            result["chapters"] = count_chapters(book)
            result["warnings"] = list(dict.fromkeys(job.notes + book.warnings))
            emit("batch_book", job.id, book)
            emit("chapters", job.id, count_chapters(book))
            cover = job.cover_path
            source = job.cover_source
            if cover and not Path(cover).is_file():
                cover = ""
                result["warnings"].append("原封面文件不存在，将重新匹配封面")
            if not cover and options.auto_cover and job.cover_mode != "generated":
                phase(job, index, "联网刮削封面")
                try:
                    from covers import search_covers, first_matching_cover

                    candidate = first_matching_cover(
                        search_covers(job.title, job.author), job.title, job.author
                    )
                    if candidate:
                        cover = candidate.path
                        source = (candidate.provider or "网络封面") + " · 自动匹配"
                        emit("cover_applied", job.id, cover, source)
                        phase(job, index, "已应用图片封面 · " + (candidate.provider or "网络来源"))
                        result["cover_match"] = candidate.match
                        if candidate.match != "书名、作者匹配":
                            result["warnings"].append("自动采用书名匹配封面，作者请核对")
                    else:
                        result["warnings"].append("未找到匹配图片封面，使用文字封面")
                        phase(job, index, "封面未匹配，使用文字封面")
                except Exception as exc:
                    result["warnings"].append("联网封面失败，使用文字封面：" + str(exc))
            if cancel.is_set():
                raise InterruptedError("已停止")
            result["cover_source"] = (
                source
                if cover
                else "指定文字封面" if job.cover_mode == "generated" else "自动文字封面"
            )
            result["cover_type"] = "图片封面" if cover else "文字封面"
            if not cover and not options.auto_cover and job.cover_mode != "generated":
                result["warnings"].append("自动匹配封面已关闭，本书使用文字封面")
            phase(job, index, "生成 EPUB")
            epub = work / "book.epub"
            result["pages"] = write_epub(
                book, epub, cover, options.line_height, options.paragraph_gap, options.cover_style
            )
            stem = safe_filename(job.title + (" - " + job.author if job.author else ""))
            if cancel.is_set():
                raise InterruptedError("已停止")
            if "epub" in options.formats:
                result["outputs"].append(publish(epub, output, stem, "epub"))
            if "mobi" in options.formats:
                phase(job, index, "编码 MOBI")
                mobi = work / "book.mobi"
                encoder_code = convert_mobi(
                    epub,
                    mobi,
                    converter,
                    cancel,
                    work / "encoder.log",
                    lambda stage: phase(job, index, stage),
                )
                result["encoder"] = Path(converter["path"]).name
                if encoder_code == 1:
                    result["warnings"].append(
                        "编码器返回兼容提示，已校验生成的 MOBI；详情见 encoder.log"
                    )
                if cancel.is_set():
                    raise InterruptedError("已停止")
                result["outputs"].append(publish(mobi, output, stem, "mobi"))
            verify_published_files(work, result["outputs"])
            result["published_verified"] = True
            maintenance.cleanup_work(work, result["outputs"])
            result["status"] = "完成"
            phase(job, index, "完成" + (" · 有提示" if result["warnings"] else ""))
        except InterruptedError as exc:
            result["status"] = "已停止"
            result["error"] = str(exc)
            emit("status", job.id, "已停止")
        except Exception as exc:
            result["status"] = "部分完成" if result["outputs"] else "失败"
            result["error"] = str(exc)
            error_log = work / "error.log"
            error_log.write_text(traceback.format_exc(), encoding="utf-8")
            result["error_log"] = str(error_log)
            phase(job, index, result["status"] + " · " + str(exc))
            emit("log", job.id, "错误日志：" + str(error_log))
        results.append(result)
        (work / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8"
        )
        emit("progress", index, len(selected))
    report = {
        "run_id": run_id,
        "output": str(output),
        "stopped": cancel.is_set(),
        "requested": len(selected),
        "results": results,
        "progress_log": str(progress_log),
    }
    report_path = archive / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    report["report_path"] = str(report_path)
    return report


def run_batch(jobs, options, emit=None, cancel=None):
    maintenance = create_maintenance(ROOT, kind="conversion")
    try:
        return _run_batch(jobs, options, emit, cancel, maintenance)
    finally:
        maintenance.finish()
