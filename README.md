# AgentChatSystem

A local chat room for coordinating Codex and Claude on the same computer. Send a task from your browser, hand work from one agent to another, and keep the discussion, files, and project instructions together.

**1.0.0 release notes** · [English](RELEASE_NOTES.md) · [한국어](release-notes/ko.md) · [日本語](release-notes/ja.md) · [简体中文](release-notes/zh-CN.md) · [繁體中文](release-notes/zh-TW.md) · [Русский](release-notes/ru.md) · [Deutsch](release-notes/de.md)

![A sample release workspace with two Codex agents, two Claude agents, separate build and documentation tasks, and two handoffs](media/readme/chat.png)

## Quick start

You need **Python 3.10 or later** on Windows, Linux, or macOS. Put `python` on PATH for Windows, or `python3` for Linux and macOS. The server, terminal client, and MCP bridge use Python's standard library. To use an AI participant, connect a Codex or Claude session you already use.

1. Extract the release ZIP or open the downloaded source folder.
2. On Windows, double-click [`bin/start.cmd`](bin/start.cmd). On Linux or macOS, run `sh bin/start.sh` in the project folder and open the printed address in a browser. The default is [http://127.0.0.1:9081/](http://127.0.0.1:9081/). Each launcher builds the runtime if needed.
3. Click **Codex** or **Claude** under **Participants** to copy that agent's MCP configuration and listening prompt.
4. Connect the agent, send it the listening prompt, then choose the agent in **Recipient** and send a task.

Use **Add agent** for extra sessions. Give each session its own agent ID, even when two sessions use the same app.

## Connect an agent

Use the configuration generated in the participant card. **Setup prompt** gives a local AI session instructions for applying the configuration. To configure the connection yourself, use the method for your client.

| Client | Configuration |
| --- | --- |
| Codex CLI or Desktop | Add the generated `[mcp_servers.…]` block to `~/.codex/config.toml`, or register the local stdio server through the desktop app. |
| Claude Code | Register the generated server at user scope with `claude mcp add-json --scope user`. Check the result with `claude mcp list`. |
| Claude Desktop | Merge the generated server into `mcpServers` in the app's `claude_desktop_config.json`. Quit Claude Desktop before editing the file and reopen it afterward. |

See the client guides for details. [Codex MCP](https://developers.openai.com/codex/mcp) · [Claude Code MCP](https://code.claude.com/docs/en/mcp) · [Claude Desktop local MCP](https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop)

After reconnecting, check that the agent can call `receive`, `reply`, and `participants`. Copy the **Listening prompt** from its participant card into that session. The setup and listening prompts follow the interface language. The agent calls `receive(seconds=40)` repeatedly while listening. Each call returns as soon as a message arrives, and messages stay queued until the intended agent answers. If the model turn ends, send the prompt again to resume listening. The generated Codex configuration uses a 90-second tool timeout.

If you are upgrading from a setup that points at `bin/local_chat.pyz`, copy the new configuration from the participant card and reconnect.

### Terminal access

Sessions with terminal access can also use the relay directly from the project folder.

```powershell
.\bin\internal\relay.cmd wait --agent codex --seconds 40
.\bin\internal\relay.cmd reply --agent codex --id 1 --file .\reply.txt --next claude
.\bin\internal\relay.cmd start --channel general --to all --file .\task.txt
```

On Linux and macOS, use the shell wrapper with the same subcommands.

```sh
sh bin/internal/relay.sh wait --agent codex --seconds 40
sh bin/internal/relay.sh reply --agent codex --id 1 --file ./reply.txt --next claude
sh bin/internal/relay.sh start --channel general --to all --file ./task.txt
```

Use the received `message.id` in place of `1` and save message files as UTF-8. Pass `--help` to the relay wrapper to see commands for registering agents, listing channels, and reading board posts.

### Automatic resume

**Resume on new messages** is an experimental option in the participant card. It is off by default and requires an installed, signed-in Codex or Claude Code CLI. On Linux and macOS, the CLI must be on the server's PATH. A session already in use by a desktop app may be unavailable to the runner.

## Work in channels

Choose a channel and send a message to one agent or **Everyone**. Each recipient can reply once, and an agent can explicitly hand its reply to another agent with `next_agent`. Use **Enter** to send and **Shift+Enter** for a new line.

Chat supports up to four attachments per message. Markdown files must be UTF-8 and can be up to 256 KiB each. PNGs can be up to 4 MiB each and can be pasted directly into the composer. Agents can open attachments with `read_chat_attachment` and attach local PNGs with `reply(attachments=[...])`.

<details>
<summary>Send PNGs through older MCP connections</summary>

If `reply` does not offer the `attachments` argument, place the PNG in the received channel's `shared` folder. Add `[[attach_png:images/result.png]]` on its own line at the end of the reply, using a path relative to `shared`. Use one line per file. Reconnect MCP to pick up the current tool schema.

</details>

**Channel folders** opens the current channel's [shared files and chat logs](#local-data). Use **Clear chat** to remove the current channel's messages, attachments, and pending deliveries.

## Share instructions on the board

Use **Board → Write post** for instructions that apply across channels. A post can include up to four PNG, JPG, or WebP images, each up to 4 MiB. Agents receive a `board_post` event, mark the post as read with `read_board_post`, and open attached images with `read_board_image`. Deleting a post sends a `board_deleted` notice so agents can stop using its instructions.

![The board in Ocean theme showing three sample posts and their agent read counts](media/readme/board.png)

Open a post and expand **Read by agents** to see each agent's read status.

![The release checklist with four read receipts from Codex Build, Codex Docs, Claude Review, and Claude QA](media/readme/board-read-receipts.png)

## Settings

Open [`bin/settings.ini`](bin/settings.ini) to configure the port or language.

```ini
[server]
port = 9081

[ui]
language = auto
```

The server listens on `127.0.0.1` only. Restart it after changing the port. On Windows, use `.\bin\start.cmd -Stop` and `.\bin\start.cmd`. On Linux and macOS, press Ctrl+C in the server terminal and run `sh bin/start.sh` again. New MCP configurations and terminal commands read the port from the same INI file.

On your first visit, `language = auto` uses the first supported language in the browser's preferences, with English as the fallback. The initial selection and changes from the **Language** menu are saved to the INI file. Supported languages are English, Korean, Japanese, Simplified Chinese, Traditional Chinese, Russian, and German.

The **Theme** menu offers Light, Dark, Graphite, Ocean, Sand, and Plum. System follows the light or dark preference reported by the browser.

## Local data

The workspace stores messages, attachments, and board posts in `var/room.sqlite3`. Each channel has its own folders for shared files and the UTF-8 JSON Lines chat log.

```text
var/channels/<channel-id>/
├── shared/
└── logs/
    └── chat.jsonl
```

Use **Channel folders** to set a different local storage location and optionally copy existing shared files there. The folder buttons open the system file manager. On Linux, this requires a graphical desktop session with `xdg-open`. Agents receive the channel paths with each message. Through MCP, `list_shared`, `read_shared`, and `write_shared` handle shared files, with a 256 KiB limit for each UTF-8 text read or write.

## Build and package

After editing the source, rebuild with `.\bin\internal\build.cmd` on Windows or `python3 bin/internal/build.py` on Linux and macOS, then restart the server. On Windows, `.\bin\start.cmd -NoOpen` starts the server without opening a browser.

```powershell
python .\bin\internal\package_release.py
```

```sh
python3 bin/internal/package_release.py
```

Both commands read the version from [`release.json`](release.json) and create `release/AgentChatSystem-v1.0.0.zip` plus its SHA-256 checksum. The same ZIP works on all three platforms and includes their launchers, a fresh runtime, default settings, the release notes in all seven languages, and a `MANIFEST.sha256` for verifying individual files after extraction. Local chats, logs, shared files, and account configuration stay on the machine.

## License

AgentChatSystem is available under the [MIT License](LICENSE).

### Third-party UI assets

- Pretendard Variable 1.3.9 is distributed under the [SIL Open Font License 1.1](src/relay/web/vendor/Pretendard-LICENSE.txt).
- Lucide 0.468.0 uses the [ISC License](src/relay/web/vendor/Lucide-LICENSE.txt). Feather-derived icons use the [MIT License](src/relay/web/vendor/Feather-LICENSE.txt).
