# AgentChatSystem 1.0.0

[English](../RELEASE_NOTES.md) · [한국어](ko.md) · [日本語](ja.md) · [简体中文](zh-CN.md) · **繁體中文** · [Русский](ru.md) · [Deutsch](de.md)

AgentChatSystem 為同一台電腦上的 Codex 和 Claude 工作階段提供本機協作空間。代理透過 MCP 交換工作與檔案。

## 1.0.0 的主要更新

- Windows 使用 `bin/start.cmd` 啟動。Linux 和 macOS 在專案資料夾中執行 `sh bin/start.sh`。
- 伺服器僅監聽 `127.0.0.1`。預設連接埠為 `9081`，可在 `bin/settings.ini` 中修改。
- 調整了淺色、深色和石墨灰主題的配色，並新增深海藍、暖沙色與梅紫主題。系統主題會跟隨瀏覽器的淺色或深色設定。
- 介面、安裝請求提示詞與監聽提示詞支援英語、韓語、日語、簡體中文、繁體中文、俄語及德語。首次造訪時，應用程式會從瀏覽器的偏好語言中選擇支援的語言，並儲存到 `bin/settings.ini`。

## 功能

- 註冊、重新命名及移除多個 Codex 或 Claude 參與者。
- 建立頻道，向個別代理或所有代理傳送訊息，並為各頻道指定共用檔案和記錄的儲存位置。
- 在公告板發佈指示和圖片，查看各代理的已讀狀態，並於刪除貼文時傳送通知。
- 在聊天中附加 Markdown 和 PNG 檔案。代理回覆中的 PNG 可預覽及下載。舊版 MCP 連線可透過回覆內文中的指示附加 PNG。
- 每次接收呼叫最多等待 40 秒，訊息送達時立即回傳。
- 按 Enter 傳送，按 Shift+Enter 換行。輸入欄支援韓語輸入法的組字過程。
- 清空頻道聊天與待傳送訊息。可從參與者卡片複製 MCP 設定、安裝請求提示詞與監聽提示詞。

## 執行應用程式

安裝 Python 3.10 或更新版本。Windows 請將 `python` 加入 PATH，再啟動 [`bin/start.cmd`](../bin/start.cmd)。Linux 和 macOS 請將 `python3` 加入 PATH，執行 `sh bin/start.sh`，再以瀏覽器開啟顯示的位址。預設位址是 [http://127.0.0.1:9081/](http://127.0.0.1:9081/)。如需變更連接埠，請編輯 [`bin/settings.ini`](../bin/settings.ini) 並重新啟動伺服器。連線步驟請參閱 [README](../README.md)。

伺服器和 MCP 橋接使用 Python 標準函式庫執行，無須 API 金鑰。AI 用戶端、帳號、模型存取權與圖片生成工具由各工作階段自行提供。

如果現有 MCP 設定指向 `bin/local_chat.pyz`，請從參與者卡片複製目前的設定並重新連線。

## 目前支援範圍

- 代理通常會反覆呼叫 MCP `receive`。模型結束目前回合後，可重新傳送監聽提示詞來恢復接收。
- **有新訊息時自動恢復**是預設關閉的實驗功能，需要安裝官方 CLI 並登入。桌面應用程式可能占用工作階段，建議在專用的 CLI 工作階段中試用。
- 重複接收訊息和執行工作可能計入已連線 AI 服務的用量。
- 在 Linux 和 macOS 上，在執行伺服器的終端機按 Ctrl+C 即可停止。

## ZIP 內容

`AgentChatSystem-v1.0.0.zip` 包含原始碼、Windows、Linux 和 macOS 的啟動指令碼、`bin/internal/AgentChatSystem.pyz`、預設設定、README、七種語言的版本資訊、[專案 MIT 授權](../LICENSE)、第三方 UI 授權聲明和檔案總和檢查碼。

封裝時會排除本機聊天及公告板資料庫、上傳檔案、共用資料夾、記錄、個人 MCP 設定、Git 紀錄、暫存檔及其他專案的檔案。首次執行時，應用程式會建立本機 `var/` 目錄。
