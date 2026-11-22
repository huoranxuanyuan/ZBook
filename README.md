# ZBook 小说转换器

适用于 Windows 10 / 11（64 位），当前版本 1.0.0。便携版解压后双击 `ZBook.exe` 即可打开。

把 TXT、Word（DOC、DOCX、RTF）和文字型 PDF 转成 EPUB 或 MOBI。导入书籍或文件夹后，可以核对书名、作者和章节目录，选好封面再批量导出。

遇到漏识别的章节，在“设置”里添加补充规则。封面可以选本地图片、用书名生成，或联网查找。联网搜封面会发送书名和作者，小说正文在本机处理。

<details>
<summary>从源码运行和打包</summary>

在 Windows x64 安装带 Tcl/Tk 的 Python 3.14.10，然后在项目目录运行以下命令。

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python -m pip install -r tools/requirements.txt
.\.venv\Scripts\python src/app.py
```

需要打包成独立的 Windows 程序时，再运行以下命令。

```powershell
.\.venv\Scripts\python -m pip install -r tools/requirements-build.txt
.\.venv\Scripts\python -m pip download --only-binary=:all: --no-deps -r tools/requirements-build.txt -d .wheels
.\.venv\Scripts\python tools/build_release.py --dist-dir release-output
```

</details>

作者 huoranxuanyuan。[项目许可](LICENSE) · [第三方组件](licenses/THIRD_PARTY_NOTICES.md)
