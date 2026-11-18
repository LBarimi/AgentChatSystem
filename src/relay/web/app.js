const el = (id) => document.getElementById(id);
const roleName = { codex: "Codex", claude: "Claude" };
const roleIcon = { codex: "terminal", claude: "bot" };
const messages = el("messages");
const scrollRegion = el("scroll-region");
const input = el("body");
const recipient = el("recipient");
const send = el("send");
const chatFileInput = el("chat-file-input");
const chatFileList = el("chat-file-list");
const joinDialog = el("join-dialog");
const setupDialog = el("setup-dialog");
const agentDeleteDialog = el("agent-delete-dialog");
const channelDialog = el("channel-dialog");
const foldersDialog = el("folders-dialog");
const theme = el("theme");
const systemTheme = matchMedia("(prefers-color-scheme: dark)");
const drafts = new Map();
const defaultChannel = "general";
let activeChannel = localStorage.getItem("relay-channel") || defaultChannel;
let current = null;
let live = false;
let sending = false;
let chatFiles = [];
let chatDraftId = null;
let roomRequest = 0;
let setupRequest = 0;
let setupAgent = null;
let participantKey = "";
let messageKey = "";
let channelKey = "";
let boardOpen = false;
let filesRequest = 0;
let filesView = { kind: "shared", path: "", offset: 0 };

theme.value = localStorage.getItem("relay-theme") || "system";
if (!theme.value) theme.value = "system";
function applyTheme() {
  const selected = theme.value === "system" ? (systemTheme.matches ? "dark" : "light") : theme.value;
  document.documentElement.dataset.theme = selected;
  document.querySelector('meta[name="theme-color"]').content = getComputedStyle(document.documentElement).getPropertyValue("--cool").trim();
}
applyTheme();
theme.addEventListener("change", () => {
  applyTheme();
  localStorage.setItem("relay-theme", theme.value);
});
systemTheme.addEventListener("change", applyTheme);

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function icon(name) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  const use = document.createElementNS(ns, "use");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  use.setAttribute("href", "/vendor/icons.svg#" + name);
  svg.append(use);
  return svg;
}

function avatar(role) {
  const avatar = element("span", "avatar " + role);
  avatar.setAttribute("aria-hidden", "true");
  avatar.append(role === "user" ? t("me") : icon(roleIcon[role]));
  return avatar;
}

function showMessage(id, message) {
  const target = el(id);
  target.textContent = message || "";
  target.hidden = !message;
}

function refreshControls() {
  const ready = live && current;
  el("add-agent").disabled = !ready;
  el("add-channel").disabled = !ready;
  el("show-folders").disabled = !current;
  el("clear-chat").disabled = !ready || current.total_messages === 0;
  for (const button of document.querySelectorAll(".channel-more")) button.disabled = !ready;
  recipient.disabled = !current?.participants.length;
  el("chat-attach").disabled = sending || !current;
  send.disabled = sending || !ready || !recipient.value || (!input.value.trim() && !chatFiles.length);
}

function showChannels() {
  closeChannelMenu();
  const nodes = current.channels.map((channel) => {
    const row = element("div", "channel-row");
    row.classList.toggle("selected", channel.id === activeChannel);
    const button = element("button", "channel-item");
    button.type = "button";
    button.dataset.channel = channel.id;
    if (channel.id === activeChannel) button.setAttribute("aria-current", "page");
    button.append(icon("hash"), element("span", "", channelName(channel)));
    const more = element("button", "channel-more", "⋮");
    more.type = "button";
    more.dataset.channelMenu = channel.id;
    more.setAttribute("aria-label", t("channelActions", { name: channelName(channel) }));
    more.setAttribute("aria-haspopup", "menu");
    more.setAttribute("aria-controls", "channel-menu");
    more.setAttribute("aria-expanded", "false");
    row.append(button, more);
    return row;
  });
  el("channel-list").replaceChildren(...nodes);
}

function channelName(channel) {
  return channel.id === defaultChannel && channel.default_name !== false ? t("general") : channel.name;
}

function showChatHeading() {
  const name = current ? channelName(current.channel) : t("channel");
  el("chat-title").textContent = name;
  document.title = appName + " — " + name;
  const pending = current?.participants.reduce((total, agent) => total + agent.pending, 0) || 0;
  el("room-state").textContent = current
    ? [t("messageCount", { count: current.total_messages }), pending && t("pendingCount", { count: pending })].filter(Boolean).join(" · ")
    : t("loading");
}

const clearChatDialog = el("clear-chat-dialog");
el("clear-chat").addEventListener("click", () => {
  if (!current) return;
  clearChatDialog.dataset.channel = current.channel.id;
  el("clear-chat-name").textContent = channelName(current.channel);
  showMessage("clear-chat-error", "");
  clearChatDialog.showModal();
});
el("clear-chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = el("clear-chat-confirm");
  if (button.disabled) return;
  button.disabled = true;
  try {
    await request("/api/channels/clear", { channel_id: clearChatDialog.dataset.channel });
    clearChatDialog.close();
    loadRoom();
  } catch (error) {
    showMessage("clear-chat-error", error.message);
  } finally {
    button.disabled = false;
  }
});

function showParticipants() {
  el("agent-count").textContent = current.participants.length;
  const waiting = current.participants.filter((agent) => agent.waiting).length;
  el("agent-summary").textContent = t("waitingCount", { count: waiting });

  const roster = el("roster");
  const focusId = roster.contains(document.activeElement) ? document.activeElement.dataset.agent : null;
  const previousScroll = roster.scrollTop;
  const groups = [];
  for (const role of ["codex", "claude"]) {
    const agents = current.participants.filter((agent) => agent.role === role);
    const section = element("section", "roster-group");
    section.append(element("h3", "", roleName[role] + " · " + agents.length));
    for (const agent of agents) {
      const status = element("span", "agent-status");
      const online = agent.waiting;
      status.classList.toggle("waiting", online);
      status.classList.toggle("pending", !online && agent.pending > 0);
      const automation = agent.automation || {};
      const autoLabels = { running: t("autoRunning"), busy: t("autoBusy"), retrying: t("autoRetrying"), blocked: t("autoBlocked") };
      const label = automation.enabled
        ? autoLabels[automation.state] || t("autoListening")
        : online ? t("listening") : t("idle");
      if (automation.enabled && automation.state !== "blocked") status.classList.add("waiting");
      if (automation.error) status.title = apiErrorMessage(automation);
      status.append(element("i"), label + (agent.pending ? " · " + t("agentPending", { count: agent.pending }) : ""));
      const details = element("span", "agent-details");
      details.append(element("strong", "", agent.name), status);
      const button = element("button", "agent-card");
      button.type = "button";
      button.dataset.agent = agent.id;
      button.setAttribute("aria-label", t("agentSetup", { name: agent.name }));
      button.append(avatar(role), details, icon("chevron-right"));
      section.append(button);
    }
    groups.push(section);
  }
  roster.replaceChildren(...groups);
  roster.scrollTop = previousScroll;
  if (focusId) roster.querySelector('[data-agent="' + CSS.escape(focusId) + '"]')?.focus({ preventScroll: true });
}

function option(value, name) {
  const node = element("option", "", name);
  node.value = value;
  return node;
}

function showRecipients() {
  const target = recipient.value || drafts.get(activeChannel)?.recipient || "codex";
  const options = current.participants.map((agent) => option(agent.id, agent.name));
  options.push(options.length ? option("all", t("all")) : option("", t("noAgents")));
  recipient.replaceChildren(...options);
  if (target === "all" || current.participants.some((agent) => agent.id === target)) recipient.value = target;
}

function messageAttachments(attachments) {
  const list = element("ul", "message-attachments");
  for (const file of attachments) {
    const item = element("li", "message-attachment");
    const address = "/api/messages/attachment?id=" + file.id;
    const name = element("a", "", file.name);
    name.href = address;
    name.target = "_blank";
    name.rel = "noopener";
    const preview = attachmentPreview(file, address);
    if (preview) item.append(preview);
    const download = element("a", "attachment-download", t("download"));
    download.href = address + "&download=1";
    download.download = file.name;
    download.setAttribute("aria-label", t("downloadFile", { name: file.name }));
    item.append(name, download);
    list.append(item);
  }
  return list;
}

function row(message, participants) {
  const sender = participants.get(message.sender);
  const to = participants.get(message.recipient);
  const item = element("li", "message");
  item.id = "message-" + message.id;
  const content = element("div");
  const meta = element("div", "message-meta");
  meta.append(element("strong", "", sender?.name || (message.sender === "user" ? t("me") : message.sender)));
  const route = element("span", "message-route");
  const target = message.recipient === "all" ? t("all") : to?.name;
  route.append(icon(target ? "arrow-right" : "check"), target || t("channelReply"));
  meta.append(route);
  if (message.reply_to) {
    const ref = element("a", "message-id", "↳ #" + message.reply_to);
    ref.href = "#message-" + message.reply_to;
    ref.title = t("goToOriginal");
    meta.append(ref);
  }
  const stamp = element("time", "", dateFormat.format(new Date(message.created_at)));
  stamp.dateTime = message.created_at;
  meta.append(stamp);
  content.append(meta);
  if (message.body) content.append(element("p", "message-body", message.body));
  if (message.attachments?.length) content.append(messageAttachments(message.attachments));
  item.append(avatar(sender?.role || "user"), content);
  return item;
}

function applyRoom(snapshot, repaint = false) {
  const boardChanged = current?.board_revision !== snapshot.board_revision;
  const lastParticipants = participantKey;
  participantKey = JSON.stringify(snapshot.participants.map(({ id, name, role }) => [id, name, role]));
  const dirtyRoster = !current || JSON.stringify(snapshot.participants) !== JSON.stringify(current.participants);
  current = snapshot;
  const channels = JSON.stringify([activeChannel, snapshot.channels]);
  if (repaint || channelKey !== channels) {
    showChannels();
    channelKey = channels;
  }
  if (repaint || dirtyRoster) showParticipants();
  if (repaint || lastParticipants !== participantKey) showRecipients();

  const count = snapshot.total_messages;
  if (boardOpen && boardChanged) loadBoard();
  if (!boardOpen) showChatHeading();
  refreshControls();

  const key = snapshot.messages.map((message) => message.id).join(",");
  if (!repaint && key === messageKey && lastParticipants === participantKey) return;
  const atBottom = scrollRegion.scrollHeight - scrollRegion.scrollTop - scrollRegion.clientHeight < 85;
  messageKey = key;
  const names = new Map([...snapshot.archived_participants, ...snapshot.participants].map((agent) => [agent.id, agent]));
  messages.replaceChildren(...snapshot.messages.map((message) => row(message, names)));
  el("empty").hidden = count > 0;
  showMessage("history-limit", count > snapshot.messages.length ? t("historyLimit") : "");
  if (atBottom) scrollRegion.scrollTop = scrollRegion.scrollHeight;
}

function connection(up) {
  live = up;
  const status = el("connection");
  status.classList.toggle("live", up);
  status.querySelector("span").textContent = up ? t("connected") : t("reconnecting");
  refreshControls();
}

async function loadRoom() {
  const requestedChannel = activeChannel;
  const requestId = ++roomRequest;
  try {
    const snapshot = await request("/api/room?channel=" + encodeURIComponent(requestedChannel));
    if (requestId === roomRequest) applyRoom(snapshot);
  } catch (error) {
    if (requestId !== roomRequest) return;
    if (error.status === 404 && requestedChannel !== defaultChannel) {
      switchChannel(defaultChannel);
      drafts.delete(requestedChannel);
    } else {
      showMessage("form-error", error.message);
    }
  }
}

function switchChannel(channelId) {
  if (channelId === activeChannel) return;
  closeChannelMenu();
  drafts.set(activeChannel, { body: input.value, recipient: recipient.value, files: chatFiles, requestId: chatDraftId });
  activeChannel = channelId;
  localStorage.setItem("relay-channel", channelId);
  current = null;
  participantKey = "";
  messageKey = "";
  channelKey = "";
  const draft = drafts.get(activeChannel);
  input.value = draft?.body || "";
  chatFiles = draft?.files || [];
  chatDraftId = draft?.requestId || null;
  showChatFiles();
  recipient.replaceChildren();
  messages.replaceChildren();
  scrollRegion.scrollTop = 0;
  el("empty").hidden = true;
  el("history-limit").hidden = true;
  el("chat-title").textContent = el("channel-list").querySelector('[data-channel="' + CSS.escape(channelId) + '"] span')?.textContent || t("channel");
  el("room-state").textContent = t("loading");
  showMessage("form-error", "");
  showSidePanel("channels", false);
  refreshControls();
  loadRoom();
}

function showChatFiles() {
  const rows = chatFiles.map(({ name }, index) => {
    const item = element("li");
    const remove = element("button", "button icon-button");
    remove.type = "button";
    remove.setAttribute("aria-label", t("removeAttachment", { name }));
    remove.append(icon("x"));
    remove.addEventListener("click", () => {
      chatFiles = chatFiles.filter((_, position) => position !== index);
      chatDraftId = null;
      showChatFiles();
      refreshControls();
    });
    item.append(element("span", "", name), remove);
    return item;
  });
  chatFileList.replaceChildren(...rows);
  chatFileList.hidden = !rows.length;
}

function addChatFiles(incoming) {
  if (!incoming.length || sending) return;
  const { files, error } = selectUploads(chatFiles, incoming);
  if (files.length !== chatFiles.length) chatDraftId = null;
  chatFiles = files;
  showMessage("form-error", error);
  showChatFiles();
  refreshControls();
}

el("chat-attach").addEventListener("click", () => chatFileInput.click());
chatFileInput.addEventListener("change", () => {
  addChatFiles(Array.from(chatFileInput.files));
  chatFileInput.value = "";
});
el("composer").addEventListener("paste", (event) => {
  const pasted = Array.from(event.clipboardData?.files || []);
  if (pasted.length && !sending) {
    event.preventDefault();
    addChatFiles(pasted);
  }
});
function chatDraftChanged() {
  chatDraftId = null;
  refreshControls();
}
input.addEventListener("input", chatDraftChanged);
recipient.addEventListener("change", chatDraftChanged);
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault();
    el("composer").requestSubmit();
  }
});
el("composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (send.disabled) return;
  sending = true;
  refreshControls();
  showMessage("form-error", "");
  const body = input.value;
  const selectedChannel = activeChannel;
  const target = recipient.value;
  chatDraftId ||= draftId();
  const requestId = chatDraftId;
  let attachments = [];
  try {
    attachments = await uploadFiles(chatFiles, (index, total) => showMessage("chat-upload-progress", t("uploadProgress", { index, total })));
    await request("/api/messages", {
      sender: "user", recipient: target, body, channel_id: selectedChannel, request_id: requestId, attachments,
    });
    if (activeChannel === selectedChannel) {
      if (chatDraftId === requestId) {
        input.value = "";
        chatFiles = [];
        chatDraftId = null;
        showChatFiles();
      }
      input.focus();
    } else if (drafts.get(selectedChannel)?.requestId === requestId) {
      drafts.set(selectedChannel, { body: "", recipient: target, files: [], requestId: null });
    }
  } catch (error) {
    if (activeChannel === selectedChannel) showMessage("form-error", error.message);
  } finally {
    await discardUploads(attachments);
    showMessage("chat-upload-progress", "");
    sending = false;
    refreshControls();
  }
});

const channelMenu = el("channel-menu");
const channelDeleteDialog = el("channel-delete-dialog");
let channelMenuButton = null;

function closeChannelMenu(restoreFocus = false) {
  const button = channelMenuButton;
  channelMenu.hidden = true;
  channelMenuButton = null;
  button?.setAttribute("aria-expanded", "false");
  if (restoreFocus && button?.isConnected) button.focus({ preventScroll: true });
}

function openChannelMenu(button) {
  if (channelMenuButton === button) return closeChannelMenu(true);
  closeChannelMenu();
  const channel = current?.channels.find(({ id }) => id === button.dataset.channelMenu);
  if (!channel) return;
  channelMenuButton = button;
  channelMenu.dataset.channel = channel.id;
  channelMenu.setAttribute("aria-label", t("channelActions", { name: channelName(channel) }));
  el("channel-delete").disabled = channel.id === defaultChannel;
  el("channel-protected").hidden = channel.id !== defaultChannel;
  button.setAttribute("aria-expanded", "true");
  channelMenu.hidden = false;
  const bounds = button.getBoundingClientRect();
  const menuBounds = channelMenu.getBoundingClientRect();
  channelMenu.style.left = Math.max(8, Math.min(bounds.right - menuBounds.width, innerWidth - menuBounds.width - 8)) + "px";
  channelMenu.style.top = Math.max(8, bounds.bottom + menuBounds.height + 8 <= innerHeight
    ? bounds.bottom + 4 : bounds.top - menuBounds.height - 4) + "px";
  el("channel-rename").focus();
}

channelMenu.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    event.preventDefault();
    closeChannelMenu(true);
  } else if (event.key === "Tab") {
    closeChannelMenu(true);
  } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
    event.preventDefault();
    const buttons = [...channelMenu.querySelectorAll("button:not(:disabled)")];
    const index = buttons.indexOf(document.activeElement);
    const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1
      : (index + (event.key === "ArrowUp" ? -1 : 1) + buttons.length) % buttons.length;
    buttons[next].focus();
  }
});
document.addEventListener("pointerdown", (event) => {
  if (!channelMenu.contains(event.target) && !event.target.closest("[data-channel-menu]")) closeChannelMenu();
});
document.addEventListener("scroll", (event) => {
  if (!channelMenu.contains(event.target)) closeChannelMenu();
}, true);
window.addEventListener("resize", () => closeChannelMenu());

el("channel-list").addEventListener("click", (event) => {
  const more = event.target.closest("[data-channel-menu]");
  if (more) return openChannelMenu(more);
  const button = event.target.closest("[data-channel]");
  if (!button) return;
  if (boardOpen) showBoard(false);
  switchChannel(button.dataset.channel);
  showSidePanel("channels", false);
  el("chat-title").focus({ preventScroll: true });
});
function editChannel(channel = null) {
  el("channel-form").reset();
  channelDialog.dataset.channel = channel?.id || "";
  const title = channel ? "renameChannel" : "addChannel";
  const submit = channel ? "save" : "create";
  el("channel-dialog-title").dataset.i18n = title;
  el("channel-dialog-title").textContent = t(title);
  el("channel-submit").dataset.i18n = submit;
  el("channel-submit").textContent = t(submit);
  if (channel) el("channel-name").value = channelName(channel);
  showMessage("channel-error", "");
  channelDialog.showModal();
  el("channel-name").focus();
  el("channel-name").select();
}
el("add-channel").addEventListener("click", () => editChannel());
el("channel-rename").addEventListener("click", () => {
  const channel = current?.channels.find(({ id }) => id === channelMenu.dataset.channel);
  closeChannelMenu(true);
  if (channel) editChannel(channel);
});
el("channel-delete").addEventListener("click", () => {
  const channel = current?.channels.find(({ id }) => id === channelMenu.dataset.channel);
  closeChannelMenu(true);
  if (!channel || channel.id === defaultChannel) return;
  channelDeleteDialog.dataset.channel = channel.id;
  el("channel-delete-name").textContent = channelName(channel);
  showMessage("channel-delete-error", "");
  channelDeleteDialog.showModal();
});
el("channel-delete-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = el("channel-delete-confirm");
  if (button.disabled) return;
  const channelId = channelDeleteDialog.dataset.channel;
  button.disabled = true;
  showMessage("channel-delete-error", "");
  try {
    await request("/api/channels/delete", { channel_id: channelId });
    channelDeleteDialog.close();
    if (activeChannel === channelId) switchChannel(defaultChannel);
    else loadRoom();
    drafts.delete(channelId);
    el("add-channel").focus({ preventScroll: true });
  } catch (error) {
    showMessage("channel-delete-error", error.message);
  } finally {
    button.disabled = false;
  }
});
el("channel-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = el("channel-submit");
  if (button.disabled) return;
  button.disabled = true;
  showMessage("channel-error", "");
  try {
    const channelId = channelDialog.dataset.channel;
    const channel = await request(channelId ? "/api/channels/rename" : "/api/channels", {
      name: el("channel-name").value, ...(channelId ? { channel_id: channelId } : {}),
    });
    channelDialog.close();
    if (channelId) loadRoom();
    else {
      switchChannel(channel.id);
      el("chat-title").textContent = channelName(channel);
    }
  } catch (error) {
    showMessage("channel-error", error.message);
  } finally {
    button.disabled = false;
  }
});

function showFolderPaths(channel) {
  foldersDialog.dataset.channel = channel.id;
  el("folders-channel").textContent = channelName(channel);
  el("folder-location").value = channel.folder;
  el("folder-default").disabled = channel.folder === channel.default_folder;
  el("shared-path").textContent = channel.shared;
  el("server-folder-settings").hidden = !["localhost", "127.0.0.1"].includes(location.hostname);
  el("logs-path").textContent = channel.logs;
}

el("show-folders").addEventListener("click", () => {
  if (!current) return;
  el("folder-form").reset();
  showFolderPaths(current.channel);
  showMessage("folder-error", "");
  el("folders-feedback").textContent = "";
  foldersDialog.showModal();
  el("files-kind").value = "shared";
  el("server-folder-settings").open = false;
  loadChannelFiles("shared");
});

async function loadChannelFiles(kind = filesView.kind, path = filesView.path, offset = filesView.offset) {
  const requestId = ++filesRequest;
  const channel = foldersDialog.dataset.channel;
  filesView = { kind, path, offset };
  const list = el("channel-files");
  list.replaceChildren();
  el("files-path").textContent = `${kind}/${path}`;
  el("files-up").disabled = !path;
  el("files-previous").disabled = true;
  el("files-next").disabled = true;
  el("files-status").textContent = t("loading");
  showMessage("files-error", "");
  try {
    const query = new URLSearchParams({ channel, kind, path, offset });
    const result = await request("/api/files?" + query);
    if (requestId !== filesRequest || !foldersDialog.open) return;
    for (const entry of result.entries) {
      const row = element("li", "channel-file");
      const name = entry.path.split("/").at(-1);
      if (entry.type === "folder") {
        const folder = element("button", "file-entry", name + "/");
        folder.type = "button";
        folder.prepend(icon("chevron-right"));
        folder.addEventListener("click", () => loadChannelFiles(kind, entry.path, 0));
        row.append(folder);
      } else {
        const info = element("span", "file-info");
        info.append(element("span", "file-name", name), element("small", "file-size", `${entry.bytes.toLocaleString()} B`));
        const link = element("a", "button secondary", t("download"));
        link.href = "/api/files/download?" + new URLSearchParams({ channel, kind, path: entry.path });
        link.download = name;
        link.setAttribute("aria-label", t("downloadFile", { name }));
        row.append(info, link);
      }
      list.append(row);
    }
    el("files-status").textContent = result.entries.length ? t("filesPage", { page: Math.floor(offset / 200) + 1 }) : t("noFiles");
    el("files-previous").disabled = offset === 0;
    el("files-next").disabled = !result.truncated;
  } catch (error) {
    if (requestId !== filesRequest || !foldersDialog.open) return;
    el("files-status").textContent = "";
    showMessage("files-error", error.message);
  }
}

el("files-kind").addEventListener("change", () => loadChannelFiles(el("files-kind").value, "", 0));
el("files-refresh").addEventListener("click", () => loadChannelFiles());
el("files-up").addEventListener("click", () => loadChannelFiles(filesView.kind, filesView.path.split("/").slice(0, -1).join("/"), 0));
el("files-previous").addEventListener("click", () => loadChannelFiles(filesView.kind, filesView.path, Math.max(0, filesView.offset - 200)));
el("files-next").addEventListener("click", () => loadChannelFiles(filesView.kind, filesView.path, filesView.offset + 200));
foldersDialog.addEventListener("close", () => { filesRequest++; });

async function saveFolder(path) {
  const channelId = foldersDialog.dataset.channel;
  const saveButton = el("folder-save");
  const defaultButton = el("folder-default");
  if (saveButton.disabled) return;
  const defaultWasDisabled = defaultButton.disabled;
  saveButton.disabled = true;
  defaultButton.disabled = true;
  showMessage("folder-error", "");
  el("folders-feedback").textContent = t("savingLocation");
  let saved;
  try {
    saved = await request("/api/channels/folder", {
      channel_id: channelId, folder: path, copy_shared: el("folder-copy-shared").checked,
    });
    if (foldersDialog.dataset.channel === channelId) {
      showFolderPaths(saved);
      loadChannelFiles(el("files-kind").value, "", 0);
      el("folders-feedback").textContent = t("locationSaved");
    }
    if (activeChannel === channelId) loadRoom();
  } catch (error) {
    if (foldersDialog.dataset.channel === channelId) {
      el("folders-feedback").textContent = "";
      showMessage("folder-error", error.message);
    }
  } finally {
    saveButton.disabled = false;
    if (!saved && foldersDialog.dataset.channel === channelId) defaultButton.disabled = defaultWasDisabled;
  }
}

el("folder-form").addEventListener("submit", (event) => {
  event.preventDefault();
  saveFolder(el("folder-location").value);
});
el("folder-default").addEventListener("click", () => saveFolder(""));

for (const button of document.querySelectorAll("[data-folder]")) {
  button.addEventListener("click", async () => {
    try {
      await request("/api/channels/open", { channel_id: foldersDialog.dataset.channel, kind: button.dataset.folder });
      el("folders-feedback").textContent = t("folderOpened");
    } catch (error) {
      el("folders-feedback").textContent = error.message;
    }
  });
}

async function openSetup(agentId) {
  const requestId = ++setupRequest;
  const language = document.documentElement.lang;
  const query = new URLSearchParams({ agent: agentId, lang: language });
  const setup = await request("/api/setup?" + query);
  if (requestId !== setupRequest) return;
  if (language !== document.documentElement.lang) return openSetup(agentId);
  setupAgent = setup.id;
  el("agent-name").value = setup.name;
  showMessage("rename-error", "");
  el("setup-title").textContent = t("connectAgent", { name: setup.name });
  el("setup-id").textContent = roleName[setup.role] + " · " + setup.id;
  el("setup-install").hidden = !setup.install_prompt;
  el("setup-install-preview").open = false;
  el("setup-install-prompt").textContent = setup.install_prompt || "";
  el("setup-install-instruction").textContent = setup.role === "codex"
    ? t("codexInstall")
    : t("claudeInstall");
  el("setup-install-feedback").textContent = "";
  el("setup-instruction").textContent = setup.role === "codex"
    ? t("codexConfigHint")
    : t("claudeConfigHint", { path: setup.config_path || "~/.claude.json" });
  el("setup-config").textContent = setup.config;
  el("setup-prompt").textContent = setup.prompt;
  el("setup-automation").checked = Boolean(setup.automation?.enabled);
  el("setup-automation-status").textContent = setup.automation?.enabled
    ? t("resumeEnabled")
    : t("resumeDisabled");
  showMessage("setup-automation-error", "");
  el("setup-feedback").textContent = "";
  if (!setupDialog.open) setupDialog.showModal();
}

el("roster").addEventListener("click", async (event) => {
  const card = event.target.closest("[data-agent]");
  if (!card) return;
  try {
    await openSetup(card.dataset.agent);
  } catch (error) {
    showMessage("form-error", error.message);
    showSidePanel("agents", false);
  }
});

el("add-agent").addEventListener("click", () => {
  el("join-form").reset();
  showMessage("join-error", "");
  joinDialog.showModal();
  el("join-name").focus();
});
el("join-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = el("join-submit");
  if (button.disabled) return;
  button.disabled = true;
  showMessage("join-error", "");
  let agent;
  try {
    agent = await request("/api/participants", { role: el("join-role").value, name: el("join-name").value });
  } catch (error) {
    showMessage("join-error", error.message);
  } finally {
    button.disabled = false;
  }
  if (!agent) return;
  joinDialog.close();
  try {
    await openSetup(agent.id);
  } catch (error) {
    showMessage("form-error", error.message);
    showSidePanel("agents", false);
  }
});

el("rename-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = el("rename-submit");
  if (!setupAgent || button.disabled) return;
  const id = setupAgent;
  button.disabled = true;
  showMessage("rename-error", "");
  try {
    const agent = await request("/api/participants/rename", { id, name: el("agent-name").value });
    if (setupAgent === id) {
      el("setup-title").textContent = t("connectAgent", { name: agent.name });
      el("agent-name").value = agent.name;
      el("setup-feedback").textContent = t("nicknameSaved");
    }
  } catch (error) {
    if (setupAgent === id) showMessage("rename-error", error.message);
  } finally {
    button.disabled = false;
  }
});

el("setup-delete").addEventListener("click", () => {
  if (!setupAgent) return;
  agentDeleteDialog.dataset.agentId = setupAgent;
  const name = current?.participants.find((agent) => agent.id === setupAgent)?.name || setupAgent;
  el("agent-delete-name").textContent = name + " (" + setupAgent + ")";
  showMessage("agent-delete-error", "");
  agentDeleteDialog.showModal();
});
el("setup-automation").addEventListener("change", async () => {
  const toggle = el("setup-automation");
  const id = setupAgent;
  const enabled = toggle.checked;
  toggle.disabled = true;
  showMessage("setup-automation-error", "");
  try {
    await request("/api/participants/automation", { id, enabled });
    if (setupAgent === id) {
      el("setup-automation-status").textContent = enabled ? t("resumeEnabled") : t("resumeOff");
    }
  } catch (error) {
    if (setupAgent === id) {
      toggle.checked = !enabled;
      showMessage("setup-automation-error", error.message);
    }
  } finally {
    toggle.disabled = false;
  }
});
el("agent-delete-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = el("agent-delete-confirm");
  if (button.disabled) return;
  button.disabled = true;
  const id = agentDeleteDialog.dataset.agentId;
  showMessage("agent-delete-error", "");
  try {
    await request("/api/participants/delete", { id });
    agentDeleteDialog.close();
    if (setupAgent === id) {
      setupDialog.close();
      setupAgent = null;
    }
    loadRoom();
    el("add-agent").focus({ preventScroll: true });
  } catch (error) {
    showMessage("agent-delete-error", error.message);
  } finally {
    button.disabled = false;
  }
});

for (const dismiss of document.querySelectorAll("[data-close]")) {
  dismiss.addEventListener("click", () => el(dismiss.dataset.close).close());
}
for (const button of document.querySelectorAll("[data-copy]")) {
  button.addEventListener("click", async () => {
    const feedback = el(button.dataset.feedback);
    const source = el(button.dataset.copy);
    try {
      await navigator.clipboard.writeText(source.textContent);
      feedback.textContent = t("copied");
    } catch {
      const preview = source.closest("details");
      if (preview) preview.open = true;
      feedback.textContent = t("copyFailed");
    }
  });
}

const sidePanels = { channels: "channels-toggle", agents: "roster-toggle" };
function showSidePanel(panel, open) {
  closeChannelMenu();
  for (const [name, toggle] of Object.entries(sidePanels)) {
    if (name !== panel && !open) continue;
    const visible = name === panel && open;
    el(name + "-panel").classList.toggle("open", visible);
    el(toggle).setAttribute("aria-expanded", String(visible));
  }
}
for (const [name, toggle] of Object.entries(sidePanels)) {
  el(toggle).addEventListener("click", () => showSidePanel(name, !el(name + "-panel").classList.contains("open")));
  el("close-" + name).addEventListener("click", () => {
    showSidePanel(name, false);
    el(toggle).focus();
  });
}

// Keep panel preferences separate from viewport clamping so a smaller window
// does not overwrite the widths chosen on a larger display.
(() => {
  const shell = document.querySelector(".app-shell");
  const limits = { channels: [180, 420], agents: [200, 480] };
  let preferred = {};
  try {
    const saved = JSON.parse(localStorage.getItem("relay-panel-widths") || "{}");
    for (const name of Object.keys(limits)) {
      if (Number.isFinite(saved?.[name])) preferred[name] = saved[name];
    }
  } catch { /* Use the default layout when saved preferences are unavailable. */ }
  const defaults = () => innerWidth <= 1080 ? { channels: 192, agents: 210 } : { channels: 218, agents: 278 };
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  function save() {
    try { localStorage.setItem("relay-panel-widths", JSON.stringify(preferred)); } catch { /* Resizing still works. */ }
  }
  function fit() {
    if (innerWidth <= 850) return;
    const normal = defaults();
    const budget = shell.clientWidth - 360;
    let left = clamp(preferred.channels ?? normal.channels, ...limits.channels);
    let right = clamp(preferred.agents ?? normal.agents, ...limits.agents);
    right = Math.max(limits.agents[0], Math.min(right, budget - left));
    left = Math.max(limits.channels[0], Math.min(left, budget - right));
    for (const [name, width, otherWidth] of [["channels", left, right], ["agents", right, left]]) {
      shell.style.setProperty(`--${name}-width`, width + "px");
      el(name + "-resizer").setAttribute("aria-valuenow", Math.round(width));
      el(name + "-resizer").setAttribute("aria-valuemax", Math.floor(Math.min(limits[name][1], budget - otherWidth)));
    }
  }
  function setWidth(name, width) {
    const other = name === "channels" ? "agents" : "channels";
    const available = shell.clientWidth - 360 - el(other + "-panel").getBoundingClientRect().width;
    preferred[name] = clamp(width, limits[name][0], Math.min(limits[name][1], available));
    fit();
  }
  for (const name of Object.keys(limits)) {
    const handle = el(name + "-resizer");
    const direction = name === "channels" ? 1 : -1;
    let drag = null;
    handle.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || innerWidth <= 850) return;
      event.preventDefault();
      closeChannelMenu();
      handle.focus({ preventScroll: true });
      drag = { id: event.pointerId, x: event.clientX, width: el(name + "-panel").getBoundingClientRect().width };
      handle.setPointerCapture(event.pointerId);
      handle.classList.add("dragging");
      document.body.classList.add("resizing-panels");
    });
    handle.addEventListener("pointermove", (event) => {
      if (drag?.id === event.pointerId) setWidth(name, drag.width + direction * (event.clientX - drag.x));
    });
    const finish = () => {
      if (!drag) return;
      drag = null;
      handle.classList.remove("dragging");
      document.body.classList.remove("resizing-panels");
      save();
    };
    for (const event of ["pointerup", "pointercancel", "lostpointercapture"]) handle.addEventListener(event, finish);
    handle.addEventListener("dblclick", () => { delete preferred[name]; fit(); save(); });
    handle.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const width = el(name + "-panel").getBoundingClientRect().width;
      const step = event.shiftKey ? 40 : 10;
      setWidth(name, event.key === "Home" ? limits[name][0] : event.key === "End" ? limits[name][1]
        : width + direction * (event.key === "ArrowRight" ? step : -step));
      save();
    });
    window.addEventListener("blur", finish);
    window.addEventListener("resize", finish);
  }
  window.addEventListener("resize", fit);
  fit();
})();

document.addEventListener("languagechange", () => {
  closeChannelMenu();
  if (current) applyRoom(current, true);
  connection(live);
  showChatFiles();
  if (foldersDialog.open) loadChannelFiles();
});

initializeLanguage().then(() => {
  document.title = appName + " — " + t("general");
  el("board-toggle").disabled = false;
  const stream = new EventSource("/api/events");
  stream.addEventListener("open", () => connection(true));
  stream.addEventListener("error", () => connection(false));
  stream.addEventListener("change", loadRoom);
});
