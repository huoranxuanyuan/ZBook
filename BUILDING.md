# 从源码运行、检查与构建

受验证的构建环境是 Windows x64、官方 Python 3.14.10（包含 Tcl/Tk 与 pip）。以下命令从仓库根目录执行；运行与构建不依赖任何编辑器进程。

## 准备环境

```text
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-build.txt
```

日常开发另安装格式检查工具：

```text
.venv\Scripts\python -m pip install -r requirements-dev.txt
```

运行桌面界面：

```text
.venv\Scripts\python app.py
```

命令行转换示例（输出在 `output`，默认不开启联网搜图）：

```text
.venv\Scripts\python cli.py examples\novel.txt --output output --formats epub mobi
```

添加 `--auto-cover` 可在缺少本地图片时联网搜封面。其他参数见 `python cli.py --help`。源码运行默认在仓库旁生成 `data/` 与 `archive/`；如需隔离，请在启动前设置 `ZBOOK_DATA_DIR`，它表示数据根目录，而不是其中的 `data` 子目录。

## 运行检查

```text
.venv\Scripts\python tools\check.py
.venv\Scripts\python -m black --check .
```

`tools/check.py` 在新的独立目录执行全部 `unittest` 回归，阻止直接网络连接，保留临时测试资料，并把测试清理动作转成隔离目录内的归档移动。默认报告在 `archive/checks/<时间与随机编号>/`；可通过 `--output-dir` 指定**新的空目录**。界面与原生拖放测试需要 Windows 桌面会话。

测试资料使用合成文本与公开测试素材。真实联网封面检索和真机阅读效果需要另外检查；离线回归通过不代表各网站始终可用。

格式配置在 [pyproject.toml](pyproject.toml)，工具版本固定在 [requirements-dev.txt](requirements-dev.txt)。新增文件要同步构建白名单；新的源码和文档也必须进入对应源码包。

## 构建便携版

先提交已经检查的源码，保持 Git 工作区干净。以下 `release-output` 必须是新的空目录：

```text
.venv\Scripts\python build_release.py --dist-dir release-output
```

该步骤生成：

- `ZBook-1.0.0-Windows-x64/`：便携程序、说明、许可证与 `release-manifest.json`。
- `ZBook-1.0.0-Windows-x64.zip`：便携压缩包。
- `ZBook-1.0.0-source.zip`：对应项目源码。

构建日志与中间文件保存在 `archive/builds/`，或用 `--work-dir` 指定新目录。程序拒绝覆盖已有发布物。清单记录依赖、编码器、作者、EXE 哈希与源码逐文件哈希；从 Git 构建时还记录提交，从不带 `.git` 的源码压缩包也可构建。

## 验收与完成组包

取得同版本的第三方源码附件，按[第三方源码说明](docs/THIRD_PARTY_SOURCES.md)核对来源。然后运行：

```text
.venv\Scripts\python tools\release.py --release-dir release-output --third-party-sources path\to\ZBook-1.0.0-third-party-sources.zip --smoke
```

工具会校验当前源码、对应源码 ZIP、便携 ZIP 与程序清单，核对第三方源码附件的固定哈希，执行实际 EXE 的 EPUB/MOBI 合成文本转换验收，再生成 `SHA256SUMS.txt`。测试输出保留在 `archive/release-checks/`。`--smoke` 使用本地合成封面，不执行在线搜图。

已有完整发布目录可只检查、不重新组包：

```text
.venv\Scripts\python tools\release.py --release-dir release-output --verify-only --smoke
```

发布到 GitHub 时，上传便携 ZIP、项目源码 ZIP、第三方源码 ZIP 和 `SHA256SUMS.txt`。对应源码必须与二进制同时可获得；仓库自身不存放发布 ZIP、工具链、个人书籍或运行资料。发布前还应实际查看封面、分卷导航和目标阅读器的显示效果。

## 维护注意事项

依赖与编码器版本固定在 `requirements*.txt` 和 `engines/kindling/origin.json`。更新后应重新审查上游、运行相关回归、更新第三方源码锁与许可证。不要只替换 EXE 而沿用旧的源码清单。

开发、测试与构建缓存不进入 Git。需要清理时移入回收站；不要将正式输出、活动浏览器配置或用户原文件作为缓存处理。
