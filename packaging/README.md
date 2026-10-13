# packaging/ — Windows EXE 发行包（绿色便携版）构建层

本目录包含把机器人打包为 Windows 11 便携 EXE 的全部构建配置。**项目业务代码零修改**，打包逻辑全部在本目录与 CI workflow 中实现。

## 交付形态

PyInstaller **onedir**（`contents_directory='.'` 扁平布局）+ 外挂 weflow-core Node 运行时 = 解压即用的绿色软件目录。不采用 onefile 单文件：`main.py` 同时依赖 CWD 的 `config.json` 与 `__file__` 定位的 `BASE_DIR`，onefile 会把两者撕裂到临时目录（详见根目录《EXE 打包可行性分析报告》）。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `run_wrapper.py` | 冻结入口：chdir 到 EXE 目录 → 注入 `sys.path`（支持用户后续丢入新插件）→ `runpy.run_module("main")` 以 `python main.py` 同等方式执行 |
| `app.spec` | PyInstaller 配置：`collect_submodules` 收齐 `core/llm/plugins/auto/services/utils/anima`（插件是 importlib 动态加载，静态分析不可见）；datas 携带 `anima/workflow.json` 与 `services/music/*.mjs`（冻结模块经 `__file__` 读取） |
| `assemble_release.py` | 组装发行目录：复制 PyInstaller 产物 + weflow-core（dist/resources/WeFlow.exe/node_modules 三件套）+ 经 `git ls-files` 枚举的运行资源（防本机私有文件泄漏）+ 生成 `start.bat` + 可选 zip |

## 本地构建（PowerShell，Python ≥3.11 且 3.12 最稳）

```powershell
python -m venv .venv-pack
.\.venv-pack\Scripts\python.exe -m pip install -r requirements.txt "pyinstaller>=6.10,<7"

# weflow-core：跳过生命周期脚本（koffi 自带 win32_x64 预编译），单独取 ffmpeg
cd weflow-core
npm ci --ignore-scripts --no-audit --no-fund
node node_modules/ffmpeg-static/install.js
node esbuild.config.mjs
Copy-Item (Get-Command node.exe).Source .\WeFlow.exe
cd ..

# 打包 + 组装 + zip
.\.venv-pack\Scripts\python.exe -m PyInstaller packaging/app.spec --noconfirm --distpath dist --workpath build
.\.venv-pack\Scripts\python.exe packaging/assemble_release.py --zip
```

产物：`release/GSMPSWechatBot-Win64/`（解压即用目录）与 `release/GSMPSWechatBot-Win64.zip`。

## 自动构建

`.github/workflows/build-exe.yml`：手动触发（workflow_dispatch）或推送 `v*` tag 时在 `windows-latest` 上执行同链路，产出走 artifact / GitHub Release。注意 runner 上没有微信客户端——CI 只验证构建与启动冒烟，真实收发需实机验收。

## 运行期要点（发行包）

- 安装/解压到**用户可写目录**（勿放 `C:\Program Files`，运行时要写 `config.json`/`data/`）。
- 用 `start.bat` 启动（含首运行配置生成与 CWD 约定）；微信需已登录且版本与 hook 组件兼容。
- 语音功能需另装 VB-CABLE（见 `scripts/install_vb_cable.bat`），纯文本不需要。
- 首次配置用 `setup_wizard.bat` 提取微信解密密钥。
- 再分发前阅读 `THIRD_PARTY_NOTICES.md`（`wcdb_api.dll` 为 CC BY-NC-SA 4.0，禁止商用；AGPL 要求随包提供对应源码）。
