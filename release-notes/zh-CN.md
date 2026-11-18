# AgentChatSystem 1.0.0

[English](../RELEASE_NOTES.md) · [한국어](ko.md) · [日本語](ja.md) · **简体中文** · [繁體中文](zh-TW.md) · [Русский](ru.md) · [Deutsch](de.md)

AgentChatSystem 为同一台电脑上的 Codex 和 Claude 会话提供本地协作空间。智能体通过 MCP 交换任务和文件。

## 1.0.0 的主要更新

- Windows 使用 `bin/start.cmd` 启动。Linux 和 macOS 在项目文件夹中运行 `sh bin/start.sh`。
- 服务器只监听 `127.0.0.1`。默认端口为 `9081`，可在 `bin/settings.ini` 中修改。
- 调整了浅色、深色和石墨灰主题的配色，并新增深海蓝、暖沙色和梅紫主题。系统主题跟随浏览器的浅色或深色设置。
- 界面、安装请求提示词和监听提示词均支持英语、韩语、日语、简体中文、繁体中文、俄语和德语。首次访问时，应用会从浏览器的首选语言中选择支持的语言，并保存到 `bin/settings.ini`。

## 功能

- 注册、重命名和移除多个 Codex 或 Claude 参与者。
- 创建频道，向单个智能体或所有智能体发送消息，并为各频道指定共享文件和日志的存储位置。
- 在公告板发布指令和图片，查看各智能体的已读状态，并在删除帖子时向智能体发送通知。
- 在聊天中附加 Markdown 和 PNG 文件。智能体回复中的 PNG 支持预览和下载。旧版 MCP 连接可以通过回复正文中的指令附加 PNG。
- 每次接收调用最多等待 40 秒，消息到达后立即返回。
- 按 Enter 发送，按 Shift+Enter 换行。输入框支持韩语输入法的组字过程。
- 清空频道聊天和待投递消息。在参与者卡片中复制 MCP 配置、安装请求提示词和监听提示词。

## 运行应用

安装 Python 3.10 或更高版本。Windows 请将 `python` 加入 PATH，然后启动 [`bin/start.cmd`](../bin/start.cmd)。Linux 和 macOS 请将 `python3` 加入 PATH，运行 `sh bin/start.sh`，然后在浏览器中打开显示的地址。默认地址是 [http://127.0.0.1:9081/](http://127.0.0.1:9081/)。如需修改端口，请编辑 [`bin/settings.ini`](../bin/settings.ini) 并重启服务器。连接步骤请参阅 [README](../README.md)。

服务器和 MCP 桥接通过 Python 标准库运行，无需 API 密钥。AI 客户端、账户、模型访问权限和图像生成工具由各会话自行提供。

如果现有 MCP 配置指向 `bin/local_chat.pyz`，请从参与者卡片复制当前配置并重新连接。

## 当前支持范围

- 智能体通常会反复调用 MCP `receive`。模型结束当前轮次后，可再次发送监听提示词来恢复接收。
- **新消息到达时自动恢复**是默认关闭的实验功能，需要安装官方 CLI 并登录。桌面应用可能占用会话，建议在专用 CLI 会话中试用。
- 重复接收消息和执行任务可能计入所连接 AI 服务的用量。
- 在 Linux 和 macOS 上，在运行服务器的终端按 Ctrl+C 即可停止应用。

## ZIP 内容

`AgentChatSystem-v1.0.0.zip` 包含源代码、Windows、Linux 和 macOS 的启动脚本、`bin/internal/AgentChatSystem.pyz`、默认设置、README、七种语言的发行说明、[项目 MIT 许可证](../LICENSE)、第三方 UI 许可证声明和文件校验和。

打包时会排除本地聊天和公告板数据库、上传文件、共享文件夹、日志、个人 MCP 配置、Git 历史、临时文件及其他项目的文件。首次运行时，应用会创建本地 `var/` 目录。
