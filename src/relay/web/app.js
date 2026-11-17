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

function readUpload(file, name = file.name) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve({ name, data: reader.result.split(",")[1] }));
    reader.addEventListener("error", () => reject(new Error(t("uploadReadFailed"))));
    reader.readAsDataURL(file);
  });
}

function refreshControls() {
  const ready = live && current;
  el("add-agent").disabled = !ready;
  el("add-channel").disabled = !ready;
  el("show-folders").disabled = !current;
  el("clear-chat").disabled = !ready || current.total_messages === 0;
  recipient.disabled = !current?.participants.length;
  el("chat-attach").disabled = sending || !current;
  send.disabled = sending || !ready || !recipient.value || (!input.value.trim() && !chatFiles.length);
}

function showChannels() {
  const nodes = current.channels.map((channel) => {
    const button = element("button", "channel-item");
    button.type = "button";
    button.dataset.channel = channel.id;
    if (channel.id === activeChannel) button.setAttribute("aria-current", "page");
    button.append(icon("hash"), element("span", "", channelName(channel)));
    return button;
  });
  el("channel-list").replaceChildren(...nodes);
}

function channelName(channel) {
  return channel.id === defaultChannel ? t("general") : channel.name;
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
    if (file.mime_type === "image/png") {
      const preview = name.cloneNode(false);
      preview.className = "attachment-preview";
      const image = element("img");
      image.src = address;
      image.alt = file.name;
      image.loading = "lazy";
      preview.append(image);
      item.append(preview);
    }
    const download = element("a", "attachment-download", t("download"));
    download.href = address;
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
    } else {
      showMessage("form-error", error.message);
    }
  }
}

function switchChannel(channelId) {
  if (channelId === activeChannel) return;
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

function addChatFiles(incoming, pasted = false) {
  if (!incoming.length || sending) return;
  const next = [...chatFiles];
  let failure = "";
  for (const file of incoming) {
    if (next.length === 4) {
      failure = t("attachmentLimit");
      break;
    }
    const name = pasted && !/\.png$/i.test(file.name) ? t("pastedImage") : file.name;
    if (/\.md$/i.test(name)) {
      if (file.size > 256 * 1024) {
        failure = t("mdLimit");
        continue;
      }
    } else if (/\.png$/i.test(name)) {
      if (!file.size || file.size > 4 * 1024 * 1024) {
        failure = t("pngLimit");
        continue;
      }
    } else {
      failure = t("attachMdPng");
      continue;
    }
    next.push({ file, name });
  }
  if (next.length !== chatFiles.length) chatDraftId = null;
  chatFiles = next;
  showMessage("form-error", failure);
  showChatFiles();
  refreshControls();
}

el("chat-attach").addEventListener("click", () => chatFileInput.click());
chatFileInput.addEventListener("change", () => {
  addChatFiles(Array.from(chatFileInput.files));
  chatFileInput.value = "";
});
el("composer").addEventListener("paste", (event) => {
  const png = Array.from(event.clipboardData?.files || []).filter((file) => file.type === "image/png");
  if (png.length && !sending) {
    event.preventDefault();
    addChatFiles(png, true);
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
  chatDraftId ||= crypto.randomUUID();
  const requestId = chatDraftId;
  try {
    const attachments = await Promise.all(chatFiles.map(({ file, name }) => readUpload(file, name)));
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
    sending = false;
    refreshControls();
  }
});

el("channel-list").addEventListener("click", (event) => {
  const button = event.target.closest("[data-channel]");
  if (!button) return;
  if (boardOpen) showBoard(false);
  switchChannel(button.dataset.channel);
  showSidePanel("channels", false);
  el("chat-title").focus({ preventScroll: true });
});
el("add-channel").addEventListener("click", () => {
  el("channel-form").reset();
  showMessage("channel-error", "");
  channelDialog.showModal();
  el("channel-name").focus();
});
el("channel-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = el("channel-submit");
  if (button.disabled) return;
  button.disabled = true;
  showMessage("channel-error", "");
  try {
    const channel = await request("/api/channels", { name: el("channel-name").value });
    channelDialog.close();
    switchChannel(channel.id);
    el("chat-title").textContent = channelName(channel);
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
  el("logs-path").textContent = channel.logs;
}

el("show-folders").addEventListener("click", () => {
  if (!current) return;
  el("folder-form").reset();
  showFolderPaths(current.channel);
  showMessage("folder-error", "");
  el("folders-feedback").textContent = "";
  foldersDialog.showModal();
});

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
  const setup = await request("/api/setup?agent=" + encodeURIComponent(agentId));
  if (requestId !== setupRequest) return;
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

document.addEventListener("languagechange", () => {
  if (current) applyRoom(current, true);
  connection(live);
  showChatFiles();
});

initializeLanguage().then(() => {
  document.title = appName + " — " + t("general");
  el("board-toggle").disabled = false;
  const stream = new EventSource("/api/events");
  stream.addEventListener("open", () => connection(true));
  stream.addEventListener("error", () => connection(false));
  stream.addEventListener("change", loadRoom);
});
