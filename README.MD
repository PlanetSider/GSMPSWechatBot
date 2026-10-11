# GSMPS Wechat Bot

**一个支持大模型对话与插件扩展的 Windows 微信机器人。**

为微信群聊提供 AI 对话、实用命令和娱乐互动。你可以配置不同群聊的回复风格、选择模型服务，并通过 Python 插件扩展机器人能力。

[快速开始](#快速开始) · [使用示例](#使用示例) · [配置说明](config_guide.md) · [开发指南](DEV_GUIDE.md) · [许可证](#许可证)

> 本项目依赖本机已登录的微信桌面客户端，通过 WeFlow 接收消息、通过桌面自动化发送消息。目前公开版本以配置文件管理，不提供 WebUI。

## 功能特性

- **AI 对话**：支持多模型服务、API 优先级与故障回退，以及按群配置身份、上下文和回复风格。
- **灵活触发**：支持前缀回复和可选的主动回复，可配置群聊白名单、时间段、黑名单与消息频率限制。
- **群聊互动**：签到、积分、抽奖、临时 Persona 和高密度抽象 Emoji 文案。
- **媒体工具**：点歌、语音、图片与 B 站视频等插件。
- **Minecraft 集成**：服务器状态、玩家信息、RCON 与相关群聊功能。
- **插件扩展**：使用 Python 添加命令，复用现有配置、消息处理与发送接口。

部分插件需要额外的 API、外部服务或设备配置。可用命令及参数以机器人内的 `/help` 为准。

公开仓库不附带部署群名、用户 ID、私有提示词、下载的音乐源脚本或 Otto 音频语料。相关功能需在本机配置；音乐源和音频资源的目录说明分别见 [music-source](music-source/README.md) 与 [Otto 资源](assets/otto/README.md)。

## 环境要求

| 组件 | 要求 |
| --- | --- |
| 操作系统 | Windows x64 |
| Python | 3.10 或更高版本 |
| Node.js | 18 或更高版本，x64，包含 npm |
| 微信 | 已登录的桌面客户端，与 WeFlow 兼容 |
| 运行环境 | 可交互的桌面会话，允许机器人操作微信窗口 |
| LLM 服务 | 使用 AI 功能时需提供模型 API |

使用语音功能时还需配置音频设备；可按需安装 VB-CABLE。原生组件依赖相应的 VC++ 运行库。

机器人发送消息时会操作窗口焦点、键盘和剪贴板，因此不适合部署在没有桌面会话的服务器上。微信与原生组件更新也可能影响兼容性。

## 快速开始

以下命令在 **PowerShell** 中执行。

### 1. 获取源码

```powershell
git clone https://github.com/oldicecube/GSMPSWechatBot.git
cd GSMPSWechatBot
```

### 2. 安装依赖

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

Push-Location weflow-core
npm.cmd ci
npm.cmd run build
Copy-Item (Get-Command node.exe).Source WeFlow.exe
Pop-Location
```

上述步骤会创建 Python 虚拟环境、构建 WeFlow 接收服务，并准备 Windows 运行入口。

### 3. 配置机器人

首次安装时复制配置模板：

```powershell
Copy-Item sample_config.json config.json
```

如果已经有 `config.json`，请保留现有文件，不要覆盖。

打开 `config.json`，填写目标群聊、触发前缀、微信连接信息和模型服务。下面是需要调整的主要字段，**不是可直接替代完整模板的配置文件**：

```json
{
  "target_group": ["你的群聊名称"],
  "prefix": ["@bot"],
  "prefix_mode": "strict",
  "weflow": {
    "dbPath": "D:\\Documents\\xwechat_files",
    "decryptKey": "你的64位十六进制解密密钥",
    "myWxid": "wxid_your_bot"
  },
  "llm": {
    "enabled": true,
    "apis": [
      {
        "name": "primary",
        "protocol": "responses",
        "model": "你的模型名称",
        "base_url": "https://api.example.com/v1",
        "api_key": "你的API密钥",
        "priority": 1
      }
    ]
  }
}
```

- 微信连接信息可通过 WeFlow 获取；微信登录后运行 `setup_wizard.bat`，按照向导完成设置。向导完成后关闭其服务，再启动机器人。
- 将模板中 `group_features`、`llm.group_profiles` 的示例群名一并替换为实际群名。
- `protocol` 可选 `openai`、`responses` 或 `anthropic`，需要与使用的 API 服务匹配。
- 不使用 AI 功能时，可将 `llm.enabled` 设为 `false`。

Minecraft 功能需配置 `mc.host`，未配置时不会连接预设服务器。`/jane` 默认使用虚构校园角色；自定义本地风格可写入被 Git 忽略的 `plugins/jane_prompt.txt`。

更多选项见 [配置说明](config_guide.md)，更新内容见 [CHANGELOG](CHANGELOG.md)。

### 4. 启动

确认微信已登录，然后运行：

```powershell
.\.venv\Scripts\python.exe main.py
```

也可以在已激活的 Python 环境中运行 `start.bat`，使用自动重试启动方式。请勿同时启动多个机器人实例。

## 使用示例

默认模板使用 `@bot` 作为前缀。将以下消息发送到已配置的目标群聊：

```text
@bot 你好，帮我解释一下这段代码
@bot /help
@bot /sign
@bot /points help
@bot /emoji 我真的服了！你到底会不会玩啊！！
@bot /song 歌曲名称
@bot /status
```

`/emoji` 会在保留原文字符的基础上插入 Emoji，也支持多行文本。插件命令的详细参数见 [命令帮助](plugins/help.txt)，管理员功能见 [管理员命令](admin_commands.txt)。

### 回复模式

默认配置使用 `strict` 模式，主动回复关闭，适合先以明确前缀触发机器人。需要机器人参与普通群聊时，可配置 `mixed` 模式及群聊主动回复选项；是否回复仍受权限、频率限制和回复判断约束。

## 常见问题

**机器人无法启动？**

先检查配置模板中的占位值是否已替换，微信是否登录，以及 Python 依赖和 WeFlow 构建是否完成。

**收到消息但没有回复？**

检查目标群名、前缀、群聊功能开关和限频设置。若已生成回复但发送失败，请确认桌面会话可用、微信窗口能够正常操作。

**原生库出现 `-101` 或 `-1000` 错误？**

仓库已包含 Windows x64 原生库修复。升级或重装覆盖文件后，先停止机器人和 WeFlow，再运行：

```powershell
.\.venv\Scripts\python.exe scripts/install_weflow_patch.py
.\.venv\Scripts\python.exe scripts/install_weflow_patch.py --check
```

修复工具会校验文件并保留备份，无需修改系统时间。组件来源与说明见 [第三方声明](THIRD_PARTY_NOTICES.md)。

**语音无法正常发送？**

检查虚拟声卡、微信麦克风与 `voice_sender` 配置。仅使用文本功能时不需要配置虚拟声卡。

如果问题仍然存在，请通过 Issue 提供复现步骤、环境版本和已脱敏的错误日志。

## 文档

| 文档 | 内容 |
| --- | --- |
| [配置说明](config_guide.md) | 配置项、群聊设置与模型服务 |
| [命令帮助](plugins/help.txt) | 用户命令及参数 |
| [管理员命令](admin_commands.txt) | 管理功能与操作说明 |
| [开发指南](DEV_GUIDE.md) | 项目结构、插件开发与测试 |
| [第三方声明](THIRD_PARTY_NOTICES.md) | 组件来源、版权与许可范围 |

## 参与贡献

欢迎提交问题反馈、功能建议和 Pull Request。

1. 提交 Issue 时说明预期行为、实际行为与复现步骤。
2. 添加或修改插件时，同步更新帮助文案，并在 Pull Request 中说明验证步骤和结果。
3. 测试脚本与测试目录仅保留本地，不随仓库分发。如果本地已有测试集，可运行：

   ```powershell
   .\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
   ```

开发环境、模块接口和测试约定详见 [开发指南](DEV_GUIDE.md)。

## 安全与隐私

- 仅在获得授权的账号和群聊中使用，并遵守微信及相关服务的使用规则。
- 启用 LLM 或外部插件时，相关消息内容可能被发送到配置的第三方服务，请提前告知参与者。
- 不要提交 API 密钥、微信解密密钥、聊天数据库、真实配置或含个人信息的日志。
- 本地接口应保持受限访问，不要直接暴露到公网。

## 致谢

本项目使用 WeFlow 提供微信消息接收能力，并采用社区原生库修复。感谢相关上游项目、依赖库作者及贡献者；具体来源见 [第三方声明](THIRD_PARTY_NOTICES.md)。

## 许可证

项目自有代码采用 **GNU AGPL-3.0-only**，详见 [LICENSE](LICENSE)。

第三方代码、原生组件和素材保留各自许可证。其中 WeFlow 与社区 DLL 补丁包含 CC BY-NC-SA 4.0 的非商业限制，不适用根项目的统一授权。使用或分发时，请同时阅读 [第三方声明](THIRD_PARTY_NOTICES.md)。
