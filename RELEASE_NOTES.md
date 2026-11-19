# AgentChatSystem 1.0.0

**English** · [한국어](release-notes/ko.md) · [日本語](release-notes/ja.md) · [简体中文](release-notes/zh-CN.md) · [繁體中文](release-notes/zh-TW.md) · [Русский](release-notes/ru.md) · [Deutsch](release-notes/de.md)

AgentChatSystem is a local collaboration workspace for Codex and Claude sessions on the same computer. Agents exchange tasks and files through MCP.

## What's new in 1.0.0

- All attachment flows accept PPT files.
- The Codex manager has a blue nickname and two agent creation buttons inside its card. On Windows, Browse opens the system folder picker. Claude options show the resolved model version.
- Agent creation buttons show which CLI is required and stay disabled when ACS cannot find that executable.
- Agent creation now includes a local folder picker and shows models and reasoning levels supplied by the installed CLI. The available reasoning levels change with the selected model.
- Managed agents start with the CLI's existing configuration. The agent card offers sign-in when the CLI reports an authentication error.
- Create and sign in to Codex parent agents directly from the channel, then add Codex or Claude Code children from the parent card. ACS resumes their CLI sessions on incoming messages, returns delegated results to the parent, and updates token counts after each turn. Removing a parent stops its running CLI and removes its children.
- Channel names now show a spinning circle while active participants have unanswered tasks. All channels update live, including unselected channels; the circle disappears when replies finish. Handoffs count, while removed participants, connection notices and board alerts do not. Reduced-motion preferences display a static ring.
- The listening prompt now calls announce_connection once at startup to post a localized “Connected.” notice under the agent’s name in its own channel. Retries with the same request ID do not duplicate the notice; timeouts, completed tasks and automatic reconnects do not repeat it. Notices do not enter other agents’ inboxes. All seven languages are supported; reconnect existing MCP clients to load the new tool.
- Channels now isolate participants, connected users, messages, boards, attachments, and MCP operations. Each channel root contains shared/, logs/, and board/; board JSON and attachments are exported below board/. New channels start without agents. Deleting a channel also removes its agents and board; shared files remain. General is still seeded only on the first database initialization.
- Existing global agents and board posts migrate once to General or the oldest remaining channel. A Recovered workspace preserves old board posts if no channels remain. Historical chat stays in place; cross-channel pending deliveries are canceled. New participants receive active board instructions only from their own channel.
- MCP registration names now use the acs_ prefix for both Codex and Claude: acs_codex and acs_claude for the defaults, acs_codex_2 and acs_claude_2 for additional agents. Configuration, setup prompts, and listening prompts use the same name while agent IDs stay unchanged. Setup prompts include matching, backup, and migration guidance for old local_chat or relay_ entries without duplicate connections. Existing clients need their configuration updated and reloaded to use the new names.
- Participant cards show a red (Disconnected) label beside agents not currently listening in the selected channel. The label disappears automatically when listening resumes, with translated labels and accessible status text in all seven languages.
- Strengthened the listening prompt in all seven languages: do not voluntarily stop without an explicit user stop request, impose no waiting or retry cap, and call receive again after timeouts or completed work. Explicit stop requests and agent removal still end listening; unavailable tools or enforced execution limits must be reported accurately.
- Channel folders now show shared/ and logs/ links that open a separate download page. Browse nested folders with breadcrumb links, parent navigation, and browser history; copy the page URL to share a folder on the same LAN. Folders appear first, with aligned names, sizes, and download actions. Server storage settings remain in the original dialog.
- Completed localization of setup, listening, and manual MCP guidance, including the embedded verification script’s messages. Language changes refresh open prompt content without discarding nickname or icon edits. Copy buttons stay disabled while loading, with a retry action on failure. CLI configuration headings follow the saved language or config --language.
- Connected users mark your computer name with (You) in yellow. Human chat messages show the sender’s computer name or IP; your own messages have yellow accents. Sender details are saved with new messages. Older messages without sender details remain labeled User.
- Customize each participant’s icon with a built-in symbol or a PNG, JPG, or GIF image up to 10 MiB. Icons are saved on the server, shared across browsers, and shown on participant cards and messages. Choose the provider logo to reset; removed participants keep their icons in existing chat history.
- Setup prompts now check the runtime environment, preserve existing settings, avoid duplicate registrations, and verify the MCP bridge with a read-only check. Claude Code uses user-scoped stdio registration with an argument list. Mandatory approvals are reported as blocked steps; permission policies are not changed.
- Moved the setup prompt to the bottom of the connection dialog. Expand Manual MCP configuration below Show prompt to view and copy the existing configuration.
- Removed the Resume on new messages setting and automatic CLI runner. Existing resume settings no longer affect MCP receiving. If a model turn ends, send the listening prompt again to continue receiving.
- Connected browser users are grouped by IP within the channel selected by each tab. Switching channels moves that tab’s presence immediately; computer names use IP as a fallback. Separate tabs can join different channels.
- Recipient defaults to Everyone separately in each channel. Manual choices are saved per channel across roster updates, language changes, and reloads. Removed targets remain unavailable until another target is selected.
- Codex cards display the last detected model and reasoning effort for the linked local session; unavailable values stay Unknown. OpenAI and Anthropic icons identify the providers.
- Drag the channel and participant panel dividers to resize them; widths are remembered in the browser.
- Use the channel’s ⋮ menu to rename or delete it. Deletion clears messages, attachments, chat logs, and pending deliveries while keeping shared files.
- `bin/settings.ini` is local and ignored by Git. First launch creates it automatically; `bin/settings.example.ini` provides the defaults.
- Windows uses `bin/start.cmd`. Linux and macOS use `sh bin/start.sh` from the project folder.
- Open the web app locally or from another device on your trusted LAN using the server computer's IPv4 address. LAN access works without a toggle or router login; public client addresses and cross-origin requests are rejected. MCP stays on loopback. The default port is `9081` and can be changed in `bin/settings.ini`.
- Chat and board posts also work over LAN HTTP, where the browser may not provide `crypto.randomUUID()`.
- Browse each channel’s `shared` and `logs` folders in the browser, including subfolders and paginated listings, and download files directly to your device. Storage location settings remain available through the loopback UI.
- Light, Dark, and Graphite have updated palettes. Ocean, Sand, and Plum are new. System follows the browser's light or dark preference.
- The interface and both connection prompts support English, Korean, Japanese, Simplified Chinese, Traditional Chinese, Russian, and German. On the first visit, the app chooses a supported language from the browser's preferences and saves the selection in `bin/settings.ini`.

## Included features

- Register, rename, and remove multiple Codex and Claude participants.
- Create channels and send messages to one agent or everyone. Set a location for each channel's shared files and logs.
- Post instructions and images to the board, check which agents have read a post, and notify agents when its instructions are deleted.
- Chat, board posts, and AI replies share the same 12 attachment extensions: PNG, JPG, TXT, CSV, XLSX, XLSM, JSON, XML, PDF, PPTX, MP4, GIF. Up to 30 files at once, 10 MiB per file. Uploads are sequential, documents have download links, and images and MP4 videos have previews. Existing attachments remain readable.
- Wait up to 40 seconds per receive call, returning as soon as a message arrives.
- Send with Enter and start a new line with Shift+Enter. The input handles Korean IME composition.
- Clear a channel's chat and pending deliveries. Copy each agent's MCP configuration, setup prompt, and listening prompt from its participant card.

## Run the app

Install Python 3.10 or later. On Windows, run [`bin/start.cmd`](bin/start.cmd) with `python` on PATH. On Linux or macOS, put `python3` on PATH and run `sh bin/start.sh`. Open the address printed by the server. The default address is [http://127.0.0.1:9081/](http://127.0.0.1:9081/). To use a different port, edit `bin/settings.ini` and restart the server. See the [README](README.md) for connection instructions.

The server and MCP bridge use Python's standard library and run without an API key. AI clients, accounts, model access, and image generation tools are provided by each session.

If an existing MCP configuration points to `bin/local_chat.pyz`, copy the current configuration from the participant card and reconnect.

## Current scope

- Agents usually keep calling MCP `receive`. If a model turn ends, send the listening prompt again to resume receiving.
- Repeated receive calls and task execution may count toward the connected AI service's usage.
- On Linux and macOS, Ctrl+C in the server terminal stops the app.

## What's in the ZIP

`AgentChatSystem-v1.0.0.zip` contains the source, launchers for Windows, Linux, and macOS, `bin/internal/AgentChatSystem.pyz`, default settings, the README, release notes in seven languages, the [project MIT license](LICENSE), third-party UI license notices, and file checksums.

The packager excludes local chat and board databases, uploads, shared folders, logs, personal MCP settings, Git history, temporary files, and unrelated project files. The app creates a local `var/` directory on first launch.
