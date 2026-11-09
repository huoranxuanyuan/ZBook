# 第三方组件与对应源码

ZBook 原创源码按 GNU Affero General Public License v3（AGPL-3.0）提供，完整文本见 LICENSE。所包含的第三方组件保留其原许可证、作者和声明；下列摘要不替代 licenses/ 内的原文。

| 组件 | 版本 | 用途及许可证 |
| --- | --- | --- |
| Python | 3.14.10 | 运行时，PSF 及随附第三方声明 |
| Tcl/Tk | 随官方 Python 运行时 | 桌面界面，BSD 风格许可 |
| Pillow | 13.0.0 | 图片与封面，HPND 及随附原生库许可 |
| requests | 2.33.1 | 网络，Apache-2.0 |
| urllib3 | 2.8.0 | HTTP/TLS，MIT |
| certifi | 2026.10.23 | CA 信任库，MPL-2.0 |
| idna | 3.15 | 域名处理，BSD-3-Clause |
| charset-normalizer | 3.4.7 | 字符编码识别，MIT |
| PyMuPDF / MuPDF | 1.28.2 | PDF、中文字体，AGPL-3.0（未购买商业许可） |
| olefile | 0.47 | Word DOC，BSD 风格许可 |
| striprtf | 0.0.35 | RTF，BSD-3-Clause |
| regex | 2026.11.7 | 有超时的自定义章节匹配，Apache-2.0/CNRI 相关声明 |
| PySocks | 1.7.1 | 用户自有 SOCKS 代理，BSD 风格许可 |
| Kindling | 0.46.2 | MOBI7/KF8 编码，MIT；原生 Rust 依赖声明一并保留 |
| PyInstaller | 6.22.3 | 打包/引导程序，GPL-2.0-or-later 加发布例外 |
| Apache POI 样例 | 见 tests/fixtures/upstream-sources.json | DOC 回归测试素材，Apache-2.0；仅源码包包含 |

Kindling 上游：https://github.com/ciscoriordan/kindling/tree/v0.46.2 。二进制来源与 SHA-256 见 engines/kindling/origin.json（源码包）和 release-manifest.json（便携包）。程序未修改 Kindling 二进制，不包含原私有版本所用的 KindleGen。

PyMuPDF 许可说明：https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright 。AGPL 分发必须保留许可与对应源码的获取方式，不能把这一组合直接改标为 MIT 闭源发行。

同一版本发布附件提供 ZBook 源码和构建脚本，以及独立的 third-party-sources.zip。后者含 Python、Python 依赖、MuPDF 原生源码、Kindling 源码与 Cargo.lock 所列依赖源码，manifest.json 记录精确版本、来源和哈希。MuPDF 字体及原生库许可、Pillow 的原生图像库许可、Python/Tcl/Tk 声明也在 licenses/ 中保留。

版权标识及第三方测试素材中的原作者信息属于各上游项目，不是本应用开发者的个人配置。所有商标属于各自所有者。

2026-11-08 依赖复核：OSV 对 12 个 Python 包及 206 个 Rust 锁定依赖未返回已知漏洞项；返回 paste 1.0.15、ttf-parser 0.25.1 的两条“停止维护”信息公告（RUSTSEC-2024-0436、RUSTSEC-2026-0192）。保留上游已测试二进制并记录后续更新事项；这些公告不是已确认的可利用漏洞。当前应用仅向引擎传入自身生成的 EPUB，不提供外部字体嵌入入口。
