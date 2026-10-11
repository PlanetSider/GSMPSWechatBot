# 版权与第三方组件说明

## 项目自有代码

Copyright (c) 2026 oldicecube and contributors.

项目自有 Python 代码、维护工具与文档采用 **GNU AGPL-3.0-only**，完整正文见根 `LICENSE`。第三方来源文件与依赖不在此重新授权范围内。原仓库 MIT 正文与版权声明留存于 `LICENSES/project-legacy-MIT.txt`；历史已发布版本的 MIT 授权不因当前改版被撤销。

## WeFlow 与社区原生补丁

- 上游项目：`https://github.com/hicccc77/WeFlow`
- 本仓库 `weflow-core/` 包含 WeFlow 派生的无头适配与原生资源；保留上游权利和许可，不能以根 AGPL 声明覆盖它们。
- 社区补丁来源：`https://github.com/AnonymousUser443/WeFlow-Community-Patch/issues/2`
- 固定提交：`57829a0391d05138f5e002a52f636c01295a378a`
- 来源文件：`binaries/wcdb_api.dll`
- 本地位置：`weflow-core/resources/wcdb/win32/x64/wcdb_api.dll`
- SHA-256：`1536606b1b1b2a0dc9de631a7f45504f5d466de0979e50b3f94548ae124993d0`
- 授权：补丁仓库明确沿用 **CC BY-NC-SA 4.0**，包括非商业使用、署名及相同方式共享条件。正文留存于 `LICENSES/CC-BY-NC-SA-4.0.txt`。
- 上游说明：此文件是对 WeFlow 5.0.0 x64 原生库两个构建过期分支的修补，不是官方产物，也不是对整个 WeFlow 的重构。本仓库直接替换该 DLL，未额外修改其二进制。
- 本项目旧 DLL 来自另一构建，不宣称新旧本地 DLL 之间只差六个字节。“六字节修补”描述的是补丁仓库对应的 5.0.0 原件。

社区 README 明确称此补丁为非商业社区修复。除过期分支外保留上游行为，包括上游原有联网/云端相关逻辑；不能据此宣称整个原生组件无联网行为。

这次更换只处理 Windows x64 的 `wcdb_api.dll`。其他架构、WCDB、密钥提取库、解密模块、运行库、SDL、FFmpeg 等资源保留各自的第三方版权及许可，需要按其实际版本确认再分发要求。社区补丁许可证不是这些全部二进制的统一授权。

## 其他来源与依赖

- `music-source/`：下载的实际脚本和凭据不随公开仓库分发，目录只提供安装说明。音乐源脚本属于各作者，项目不会将其重新授权为 AGPL；使用/更新/分发前查看原始来源及相应许可。
- `assets/`：已有通用字体、图片等素材可能有独立权利，不表示项目取得其全部再分发或商业授权。`assets/otto/` 的音频语料及索引仅本地保留，公开版本只有资源布局说明。
- Python 依赖见 `requirements.txt`，Node 依赖见 `weflow-core/package.json` 与锁文件。依赖在安装目录中附有各自许可证，不适用根项目的统一许可。

本说明是来源与范围记录，不构成已经解决所有第三方组合授权的保证。AGPL 自有代码与带非商业限制的第三方组件不能直接视为“完整组合包统一 AGPL / 可任意商用”；发布组合包或商业使用前应核对各组件授权及兼容性，必要时取得单独许可。
