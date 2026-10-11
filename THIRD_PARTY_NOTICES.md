# Third-Party Notices

本项目使用和适配了第三方软件、原生组件及素材。感谢这些项目的作者与贡献者。

本文件说明第三方组件的来源和许可范围。除另有明确说明外，第三方内容保留原作者的版权及许可证，不适用本项目自有代码的统一授权。

## 项目许可证

Copyright (c) 2026 oldicecube and contributors.

项目自有代码与文档采用 **GNU Affero General Public License v3.0 only（AGPL-3.0-only）**，完整条款见 [LICENSE](LICENSE)。

历史版本的 MIT 声明保留在 [LICENSES/project-legacy-MIT.txt](LICENSES/project-legacy-MIT.txt)。本项目当前许可证不撤销已经按照旧许可证授予的权利，也不改变第三方组件的原有授权。

## WeFlow

| 项目 | 说明 |
| --- | --- |
| 上游 | [hicccc77/WeFlow](https://github.com/hicccc77/WeFlow) |
| 仓库目录 | `weflow-core/` |
| 用途 | 微信数据库访问、消息解析、查询及事件推送 |
| 适配 | 无头运行、Node 构建、Python 机器人集成及本地 HTTP/SSE 接口 |
| 许可范围 | 上游代码和资源保留其原有版权与许可；本项目根许可证不对这些组件重新授权 |

本项目不是 WeFlow 的官方发行版。使用、修改或再分发 WeFlow 派生代码及资源时，请同时查阅对应上游版本的许可证和版权声明。

### 社区原生库修复

Windows x64 的 `wcdb_api.dll` 使用以下社区修补版本：

| 项目 | 说明 |
| --- | --- |
| 来源 | [WeFlow Community Patch](https://github.com/AnonymousUser443/WeFlow-Community-Patch) |
| 修复说明 | [Native expiry gate / Issue #2](https://github.com/AnonymousUser443/WeFlow-Community-Patch/issues/2) |
| 来源提交 | `57829a0391d05138f5e002a52f636c01295a378a` |
| 来源文件 | `binaries/wcdb_api.dll` |
| 仓库路径 | `weflow-core/resources/wcdb/win32/x64/wcdb_api.dll` |
| SHA-256 | `1536606b1b1b2a0dc9de631a7f45504f5d466de0979e50b3f94548ae124993d0` |
| 许可证 | **Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International（CC BY-NC-SA 4.0）** |

该文件用于修复上游原生组件的构建过期检查，不是上游官方产物。本项目使用来源文件，未另行修改其二进制。

许可证正文见 [LICENSES/CC-BY-NC-SA-4.0.txt](LICENSES/CC-BY-NC-SA-4.0.txt)。使用或分享该组件时，应遵守署名、非商业使用及相同方式共享等条款；该许可证不因此成为仓库内所有原生组件的统一许可证。

## 原生组件与运行时

`weflow-core/resources/` 中包含数据库、密钥提取、媒体解码及运行时组件，例如 WCDB、SDL、平台原生模块和 Microsoft VC++ 运行库。FFmpeg 等组件也可能通过依赖安装获得。

这些组件分别受其对应版本的版权、许可证或再分发条款约束。安装或打包时，请按具体文件及构建版本核实要求，不应将它们统一标记为 AGPL 或 CC BY-NC-SA。

## 软件依赖

Python 和 Node.js 依赖保留各自的许可证。依赖清单与锁定版本可在以下文件中查询：

- [requirements.txt](requirements.txt)：Python 运行依赖。
- [weflow-core/package.json](weflow-core/package.json)：Node.js 运行与构建依赖。
- [weflow-core/package-lock.json](weflow-core/package-lock.json)：Node.js 依赖版本记录。

打包依赖时，应保留适用的许可证文件与版权声明，并按照对应版本的分发要求处理。根目录 `LICENSE` 不代替这些依赖的许可证。

## 字体、图片与音频素材

`assets/` 中的字体、图片及角色素材可能具有独立的著作权、商标或其他使用限制。源码的许可并不自动授予这些素材的全部再分发、商业使用或角色形象使用权。

Otto 音频素材和索引不随公开仓库分发。启用相关功能时，应自行准备有权使用的音频资源；资源布局见 [assets/otto/README.md](assets/otto/README.md)。

请根据素材的原始来源和授权说明确认使用范围。无法确认授权的素材，不应仅依据本项目的开源许可证进行再分发。

## 音乐源与外部服务

公开仓库提供音乐源适配代码和 [安装说明](music-source/README.md)，不附带下载的音乐源脚本、访问凭据或私人代理配置。第三方脚本保留其作者的版权及许可，不由本项目重新授权。

模型、音乐、视频、行情等外部服务还可能适用各自的服务条款、访问限制和内容授权要求。使用适配器不代表本项目或上游服务授予了对其内容的额外权利。

## 使用与再分发

使用或发布包含第三方组件的版本时，请分别确认所包含组件的许可证、版权声明及使用限制。

尤其是社区 DLL 修复包含 **非商业限制**，不能将含有该组件的完整组合包视为“统一 AGPL 授权、可任意商用”。商业使用或再分发前，应确认相应授权及许可证兼容性，必要时向相关权利人取得单独许可。

如发现组件来源、署名或许可说明有遗漏，请通过仓库 Issue 提供原始来源、版本及许可证依据，以便补充或修正。
