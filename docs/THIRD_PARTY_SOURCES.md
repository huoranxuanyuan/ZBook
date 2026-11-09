# 第三方源码附件

每次发布同时提供项目源码 ZIP 与 `ZBook-1.0.0-third-party-sources.zip`。后者较大，作为 Release 附件分发，不放入普通 Git 历史。

## 固定输入

当前附件的文件名、字节数和 SHA-256 固定在仓库根目录的 [third-party-sources.json](../third-party-sources.json)。`tools/release.py` 完成组包前会检查它；不能用同名但内容不同的文件代替。

从同版本 Release 下载该附件，或使用已经保留且哈希相同的副本。该附件的内容包括：

- Python、Python 包与 MuPDF 的原始源码。
- Kindling 的原始源码和锁定 Rust 依赖的 `.crate` 文件。
- `third-party-sources/manifest.json`：各源码文件的上游 URL、字节数与 SHA-256。

## 核验和重新取得来源

可以用 Python 计算外层 ZIP 的 SHA-256；完整发布工具也会执行该检查。内部清单保留上游地址与逐文件哈希。若需从上游重新取得源码，应按照清单下载到新的空目录，核对每个文件，再按各上游说明构建。重新压缩可能改变外层 ZIP 字节，因此不能直接冒用原有附件哈希；应重新审查、更新仓库锁文件，并在新的发布记录中注明。

应用构建直接使用仓库内经哈希检查的 Kindling 二进制和固定 Python 包。第三方源码附件用于保留对应源码和重建资料，不会在 ZBook 启动时自动解压或执行。

第三方作者与许可见 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) 和 `licenses/`。更新任何依赖时，都应同步检查对应源码附件、锁文件、构建依赖和许可证，不能只更新 Python 依赖版本。
