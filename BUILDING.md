# 从源码构建

维护基线为本目录。使用 Windows x64、官方 Python 3.14.10（包含 Tcl/Tk 和 pip）。不依赖任何编辑器、AI 客户端或其进程；关闭编辑器不会影响已启动的应用。

1. 创建独立 Python 环境，执行 `python -m pip install -r requirements-build.txt`。
2. 运行 `python -m unittest discover -p "test_*.py"`。界面测试需要桌面会话；测试会使用临时目录。正式发布前应在隔离数据目录执行测试。
3. 运行 `python build_release.py --dist-dir <新的空发布目录>`。程序拒绝覆盖已有发布目录，输出单文件便携程序、便携 ZIP、对应源码 ZIP 及真实版本/哈希清单。
4. 以 `ZBOOK_DATA_DIR` 指定新的验收目录，运行 `ZBook.exe --smoke-test <公开测试TXT> <测试输出目录>`；加 `--network` 可同时检验真实封面检索。不要使用私人书籍做公开样例。
5. 核对独立阅读器的 EPUB/MOBI 正文、末段、分卷目录和封面；发布可验证的 SHA256SUMS.txt。上传 Windows-x64.zip、source.zip、third-party-sources.zip，二进制与对应源码必须同版本可获得。

固定 Python 包版本见 requirements*.txt，Kindling 的来源及哈希见 engines/kindling/origin.json。更新依赖时先审查上游公告并重新完成回归与打包验收。来自 PyPI 的原始源码与 Kindling Rust 依赖归档在 third-party-sources 发布附件；依照上游构建说明可以重新构建第三方组件。

只提交源文件、必要素材、固定版本依赖清单和许可证。禁止提交 data/、archive/、个人输出、浏览器配置、维护缓存、构建环境或旧发布目录。.gitignore 只防止新文件误加入，已被跟踪的数据仍需单独检查。新增源文件时同步 build_release.py 的发布白名单。

从 Git 仓库构建正式包前，先提交已经检查的改动并保持工作区干净。`release-manifest.json` 记录 Git 提交、软件作者及逐文件源码哈希，便于核对发布内容；从不带 `.git` 的源码压缩包也可以构建，此时提交信息为空，以源码哈希清单核对。

构建会在 maintenance/ 下产生日志和缓存，保留供验收；验收结束后归档。需要删除时移入回收站。toolchain/ 是后续更新所需的本地构建环境，不随便携包发布。
