# 第三方组件

ZBook 原创部分由 huoranxuanyuan 编写，采用 GNU AGPL-3.0-only。完整许可见 [LICENSE](LICENSE)。第三方许可按组件整理，每段保留上游文件名、版权声明和原文。[许可原文](licenses/)

| 组件 | 用途 | 许可 |
| --- | --- | --- |
| CPython、Tcl/Tk | Python 运行时和桌面界面 | PSF、Tcl/Tk 随附许可 |
| PyMuPDF-Lite、MuPDF | 读取 PDF 和字体 | AGPL-3.0，内含字体及原生库各有许可 |
| Pillow | 读取和处理封面 | HPND 及随附原生库许可 |
| requests、urllib3、certifi、idna、charset-normalizer、PySocks | HTTPS 请求、证书、编码识别及代理 | Apache-2.0、MIT、MPL-2.0、BSD 等，按组件列于 licenses |
| olefile、striprtf、regex | Word、RTF 和章节规则 | BSD、Apache-2.0 等，按组件列于 licenses |
| Kindling 及其 Rust 依赖 | 写入 MOBI7 和 KF8 | Kindling 采用 MIT，各依赖保留原许可 |
| OpenSSL、Brotli | TLS 和文档解压 | Apache-2.0、MIT |
| PyInstaller | Windows 打包 | GPL-2.0-or-later，含分发例外 |

正式分发 Windows 版时，同版本的项目源码和第三方对应源码随发布附件提供。第三方源码包含上游地址、版本和逐项 SHA-256，Kindling 使用上游发布的 Windows 二进制。[Kindling 上游](https://github.com/ciscoriordan/kindling/tree/v0.46.2)
