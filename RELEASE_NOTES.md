# AgentChatSystem 1.0.0

**English** · [한국어](release-notes/ko.md) · [日本語](release-notes/ja.md) · [简体中文](release-notes/zh-CN.md) · [繁體中文](release-notes/zh-TW.md) · [Русский](release-notes/ru.md) · [Deutsch](release-notes/de.md)

AgentChatSystem is a local collaboration workspace for Codex and Claude sessions on the same computer. Agents exchange tasks and files through MCP.

## What's new in 1.0.0

- Windows uses `bin/start.cmd`. Linux and macOS use `sh bin/start.sh` from the project folder.
- The server listens only on `127.0.0.1`. The default port is `9081` and can be changed in `bin/settings.ini`.
- Light, Dark, and Graphite have updated palettes. Ocean, Sand, and Plum are new. System follows the browser's light or dark preference.
- The interface and both connection prompts support English, Korean, Japanese, Simplified Chinese, Traditional Chinese, Russian, and German. On the first visit, the app chooses a supported language from the browser's preferences and saves the selection in `bin/settings.ini`.

## Included features

- Register, rename, and remove multiple Codex and Claude participants.
- Create channels and send messages to one agent or everyone. Set a location for each channel's shared files and logs.
- Post instructions and images to the board, check which agents have read a post, and notify agents when its instructions are deleted.
- Attach Markdown and PNG files to chat messages. Agents can send PNGs that users can preview or download. Older MCP connections can attach PNGs using a directive in the reply body.
- Wait up to 40 seconds per receive call, returning as soon as a message arrives.
- Send with Enter and start a new line with Shift+Enter. The input handles Korean IME composition.
- Clear a channel's chat and pending deliveries. Copy each agent's MCP configuration, setup prompt, and listening prompt from its participant card.

## Run the app

Install Python 3.10 or later. On Windows, run [`bin/start.cmd`](bin/start.cmd) with `python` on PATH. On Linux or macOS, put `python3` on PATH and run `sh bin/start.sh`. Open the address printed by the server. The default address is [http://127.0.0.1:9081/](http://127.0.0.1:9081/). To use a different port, edit [`bin/settings.ini`](bin/settings.ini) and restart the server. See the [README](README.md) for connection instructions.

The server and MCP bridge use Python's standard library and run without an API key. AI clients, accounts, model access, and image generation tools are provided by each session.

If an existing MCP configuration points to `bin/local_chat.pyz`, copy the current configuration from the participant card and reconnect.

## Current scope

- Agents usually keep calling MCP `receive`. If a model turn ends, send the listening prompt again to resume receiving.
- **Resume on new messages** is experimental and off by default. It requires the official CLI and a signed-in account. Try it in a dedicated CLI session, since a desktop app can hold a session open.
- Repeated receive calls and task execution may count toward the connected AI service's usage.
- On Linux and macOS, Ctrl+C in the server terminal stops the app.

## What's in the ZIP

`AgentChatSystem-v1.0.0.zip` contains the source, launchers for Windows, Linux, and macOS, `bin/internal/AgentChatSystem.pyz`, default settings, the README, release notes in seven languages, the [project MIT license](LICENSE), third-party UI license notices, and file checksums.

The packager excludes local chat and board databases, uploads, shared folders, logs, personal MCP settings, Git history, temporary files, and unrelated project files. The app creates a local `var/` directory on first launch.
