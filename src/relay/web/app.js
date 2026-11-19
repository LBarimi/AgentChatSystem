const el = (id) => document.getElementById(id);
const roleName = { codex: "Codex", claude: "Claude" };
const messages = el("messages");
const scrollRegion = el("scroll-region");
const input = el("body");
const recipient = el("recipient");
const defaultChannel = "general";
let activeChannel = localStorage.getItem("relay-channel") ?? defaultChannel;
let recipientChoice = new RecipientSelection(localStorage, activeChannel);
if (recipientChoice.id !== "all") {
  recipient.replaceChildren(new Option(recipientChoice.name || recipientChoice.id, recipientChoice.id, true, true));
}
const send = el("send");
const chatFileInput = el("chat-file-input");
const chatFileList = el("chat-file-list");
const joinDialog = el("join-dialog");
const setupDialog = el("setup-dialog");
const agentDeleteDialog = el("agent-delete-dialog");
const channelDialog = el("channel-dialog");
const foldersDialog = el("folders-dialog");
const drafts = new Map();
let current = null;
let live = false;
let sending = false;
let chatFiles = [];
let chatDraftId = null;
let roomRequest = 0;
let setupRequest = 0;
let setupAgent = null;
let iconDraft = null;
let iconSaving = false;
const participantIconLabels = {
  provider: "iconProvider", bot: "iconBot", terminal: "iconTerminal", command: "iconCommand",
  radio: "iconRadio", "message-square": "iconMessage", hash: "iconHash", users: "iconUsers", image: "iconImage",
};
let participantKey = "";
let messageKey = "";
let channelKey = "";
let boardOpen = false;
let roomStream = null;
const browserClient = draftId();

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

function userAvatar(own) {
  const node = element("span", "avatar user" + (own ? " is-self" : ""));
  node.setAttribute("aria-hidden", "true");
  node.append(own ? t("me") : icon("users"));
  return node;
}

function providerIcon(role) {
  const image = element("img", "provider-icon");
  image.src = "/vendor/" + (role === "codex" ? "openai" : "anthropic") + ".svg";
  image.alt = "";
  image.width = 20;
  image.height = 20;
  image.setAttribute("aria-hidden", "true");
  return image;
}

function participantAvatar(agent, previewUrl = "") {
  const node = element("span", "avatar provider-avatar");
  node.setAttribute("aria-hidden", "true");
  if (previewUrl || /^image:[a-f0-9]{64}$/.test(agent.icon || "")) {
    const image = element("img", "participant-image");
    image.alt = "";
    image.src = previewUrl || "/api/participants/icon?" + new URLSearchParams({ agent: agent.id, channel: activeChannel, v: agent.icon.slice(6) });
    image.addEventListener("error", () => node.replaceChildren(providerIcon(agent.role)), { once: true });
    node.append(image);
  } else {
    node.append(agent.icon !== "provider" && participantIconLabels[agent.icon] ? icon(agent.icon) : providerIcon(agent.role));
  }
  return node;
}

function modelSettings(agent) {
  const settings = agent.model_settings || {};
  const effortKeys = {
    none: "reasoningNone", minimal: "reasoningMinimal", low: "reasoningLow", medium: "reasoningMedium",
    high: "reasoningHigh", xhigh: "reasoningXhigh", max: "reasoningMax", ultra: "reasoningUltra",
  };
  const effort = settings.reasoning_effort;
  const fields = element("span", "agent-model-info");
  fields.title = t("modelSettingsHint");
  for (const [label, value] of [
    [t("model"), settings.model || t("modelUnknown")],
    [t("reasoningEffort"), effort ? (effortKeys[effort] ? t(effortKeys[effort]) : effort) : t("modelUnknown")],
  ]) {
    const field = element("span", "agent-field");
    field.append(element("span", "agent-field-label", label), element("span", "agent-field-value", value));
    fields.append(field);
  }
  return fields;
}

function showMessage(id, message) {
  const target = el(id);
  target.textContent = message || "";
  target.hidden = !message;
}

function refreshControls() {
  const ready = live && current;
  const hasChannel = Boolean(current?.channel);
  el("add-agent").disabled = !ready || !hasChannel;
  el("add-channel").disabled = !ready;
  el("empty-add-channel").disabled = !ready;
  el("board-toggle").disabled = !ready || !hasChannel;
  el("show-folders").disabled = !hasChannel;
  el("clear-chat").disabled = !ready || !hasChannel || current.total_messages === 0;
  for (const button of document.querySelectorAll(".channel-more")) button.disabled = !ready;
  recipient.disabled = !hasChannel;
  el("chat-attach").disabled = sending || !hasChannel;
  send.disabled = sending || !ready || !hasChannel || !recipientChoice.available(current.participants) || (!input.value.trim() && !chatFiles.length);
}

function showChannelView() {
  const hasChannel = Boolean(activeChannel);
  const shell = document.querySelector(".app-shell");
  const layoutChanged = shell.classList.contains("no-channel") === hasChannel;
  shell.classList.toggle("no-channel", !hasChannel);
  el("agents-panel").hidden = !hasChannel;
  el("roster-toggle").hidden = !hasChannel;
  scrollRegion.hidden = boardOpen || !hasChannel;
  el("composer").parentElement.hidden = boardOpen || !hasChannel;
  el("no-channel").hidden = boardOpen || hasChannel;
  el("show-folders").hidden = boardOpen || !hasChannel;
  el("clear-chat").hidden = boardOpen || !hasChannel;
  el("board-toggle").hidden = !hasChannel;
  if (!hasChannel && el("agents-panel").classList.contains("open")) showSidePanel("agents", false);
  if (layoutChanged) window.dispatchEvent(new Event("resize"));
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
    button.append(icon("hash"), element("span", "channel-name", channelName(channel)));
    if (channel.pending > 0) {
      const activity = element("span", "channel-activity");
      const status = t("channelWorking", { count: channel.pending });
      activity.title = status;
      activity.setAttribute("aria-hidden", "true");
      button.setAttribute("aria-label", channelName(channel) + " · " + status);
      button.append(activity);
    }
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
  const name = current?.channel ? channelName(current.channel) : t(activeChannel ? "channel" : "noChannelSelected");
  el("chat-title").textContent = name;
  document.title = appName + " — " + name;
  const pending = current?.participants.reduce((total, agent) => total + agent.pending, 0) || 0;
  el("room-state").textContent = !activeChannel ? "" : current?.channel
    ? [t("messageCount", { count: current.total_messages }), pending && t("pendingCount", { count: pending })].filter(Boolean).join(" · ")
    : t("loading");
}

const clearChatDialog = el("clear-chat-dialog");
el("clear-chat").addEventListener("click", () => {
  if (!current?.channel) return;
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
    const heading = element("h3");
    heading.append(providerIcon(role), element("span", "", roleName[role] + " · " + agents.length));
    section.append(heading);
    for (const agent of agents) {
      const status = element("span", "agent-status");
      const online = agent.waiting;
      status.classList.toggle("waiting", online);
      status.classList.toggle("pending", !online && agent.pending > 0);
      const label = online ? t("listening") : t("idle");
      status.append(element("i"), label + (agent.pending ? " · " + t("agentPending", { count: agent.pending }) : ""));
      const details = element("span", "agent-details");
      const nameLine = element("span", "agent-name-line");
      const name = element("strong", "", agent.name);
      name.title = agent.name;
      nameLine.append(name);
      if (!online) {
        const disconnected = element("span", "agent-disconnected", t("agentDisconnected"));
        disconnected.title = t("agentDisconnectedHint");
        nameLine.append(disconnected);
      }
      details.append(nameLine, status);
      const button = element("button", "agent-card");
      button.type = "button";
      button.dataset.agent = agent.id;
      button.setAttribute("aria-label", t("agentSetup", { name: agent.name }) + (online ? "" : " " + t("agentDisconnected")));
      button.append(participantAvatar(agent), details, icon("chevron-right"));
      if (role === "codex") button.append(modelSettings(agent));
      section.append(button);
    }
    groups.push(section);
  }
  roster.replaceChildren(...groups);
  roster.scrollTop = previousScroll;
  if (focusId) roster.querySelector('[data-agent="' + CSS.escape(focusId) + '"]')?.focus({ preventScroll: true });
}

function showUsers() {
  const users = live ? current?.users || [] : [];
  el("user-count").textContent = users.length;
  const list = el("user-list");
  const previousScroll = list.scrollTop;
  list.replaceChildren(...users.map((user) => {
    const item = element("li", "user-card");
    item.classList.toggle("is-self", isSelfUser(user.ip, current.viewer));
    const details = element("span", "user-details");
    details.append(element("strong", "", userLabel(user.name, user.ip, current.viewer, t)));
    if (user.name !== user.ip) details.append(element("small", "", user.ip));
    const online = element("span", "user-online");
    online.title = t("connected");
    item.append(icon("users"), details, online);
    return item;
  }));
  list.scrollTop = previousScroll;
  el("users-empty").hidden = users.length > 0;
  el("users-empty").textContent = t(live ? "noConnectedUsers" : "reconnecting");
}

function option(value, name) {
  const node = element("option", "", name);
  node.value = value;
  return node;
}

function showRecipients() {
  const options = recipientChoice.options(current.participants, t).map(({ id, name, disabled }) => {
    const node = option(id, name);
    node.disabled = Boolean(disabled);
    return node;
  });
  recipient.replaceChildren(...options);
  recipient.value = recipientChoice.id;
}

function messageAttachments(attachments) {
  const list = element("ul", "message-attachments");
  for (const file of attachments) {
    const item = element("li", "message-attachment");
    const address = "/api/messages/attachment?" + new URLSearchParams({ id: file.id, channel: activeChannel });
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
  const user = chatUserIdentity(message, current?.viewer, current?.users || [], t);
  const to = participants.get(message.recipient);
  const item = element("li", "message");
  item.classList.toggle("is-self", user.own);
  item.id = "message-" + message.id;
  const content = element("div");
  const meta = element("div", "message-meta");
  meta.append(element("strong", "", sender?.name || (message.sender === "user" ? user.label : message.sender)));
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
  item.append(sender ? participantAvatar(sender) : userAvatar(user.own), content);
  return item;
}

function applyRoom(snapshot, repaint = false) {
  const boardChanged = current?.board_revision !== snapshot.board_revision;
  const lastParticipants = participantKey;
  participantKey = JSON.stringify(snapshot.participants.map(({ id, name, role, icon }) => [id, name, role, icon]));
  const dirtyRoster = !current || JSON.stringify(snapshot.participants) !== JSON.stringify(current.participants);
  const dirtyUsers = !current || JSON.stringify([snapshot.viewer, snapshot.users]) !== JSON.stringify([current.viewer, current.users]);
  current = snapshot;
  const channels = JSON.stringify([activeChannel, snapshot.channels]);
  if (repaint || channelKey !== channels) {
    showChannels();
    channelKey = channels;
  }
  if (repaint || dirtyRoster) showParticipants();
  if (repaint || dirtyUsers) showUsers();
  if (repaint || lastParticipants !== participantKey) showRecipients();

  const count = snapshot.total_messages;
  if (boardOpen && boardChanged) loadBoard();
  if (!boardOpen) showChatHeading();
  showChannelView();
  refreshControls();

  const key = snapshot.messages.map((message) => message.id).join(",");
  if (!repaint && !dirtyUsers && key === messageKey && lastParticipants === participantKey) return;
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
  showUsers();
  refreshControls();
}

function connectRoomStream() {
  roomStream?.close();
  connection(false);
  const stream = new EventSource("/api/events?" + new URLSearchParams({ channel: activeChannel, client: browserClient }));
  roomStream = stream;
  stream.addEventListener("open", () => { if (roomStream === stream) connection(true); });
  stream.addEventListener("error", () => { if (roomStream === stream) connection(false); });
  stream.addEventListener("change", () => { if (roomStream === stream) loadRoom(); });
}

async function loadRoom() {
  const requestedChannel = activeChannel;
  const requestId = ++roomRequest;
  try {
    const snapshot = await request("/api/room?channel=" + encodeURIComponent(requestedChannel));
    if (requestId === roomRequest) applyRoom(snapshot);
  } catch (error) {
    if (requestId !== roomRequest) return;
    if (error.status === 404 && requestedChannel) {
      switchChannel("");
      drafts.delete(requestedChannel);
    } else {
      showMessage("form-error", error.message);
    }
  }
}

function switchChannel(channelId) {
  if (channelId === activeChannel) return;
  closeChannelMenu();
  resetBoardChannel();
  setupRequest++;
  for (const dialog of [clearChatDialog, foldersDialog, setupDialog, joinDialog, agentDeleteDialog, channelDeleteDialog]) dialog.close();
  if (channelDialog.dataset.channel) channelDialog.close();
  if (activeChannel) drafts.set(activeChannel, { body: input.value, files: chatFiles, requestId: chatDraftId });
  activeChannel = channelId;
  recipientChoice = new RecipientSelection(localStorage, channelId);
  localStorage.setItem("relay-channel", channelId);
  current = null;
  participantKey = "";
  messageKey = "";
  channelKey = "";
  el("roster").replaceChildren();
  el("agent-count").textContent = "0";
  el("agent-summary").textContent = t("loading");
  recipient.replaceChildren(new Option(recipientChoice.id === "all" ? t("all") : recipientChoice.name || recipientChoice.id, recipientChoice.id, true, true));
  const draft = drafts.get(activeChannel);
  input.value = draft?.body || "";
  chatFiles = draft?.files || [];
  chatDraftId = draft?.requestId || null;
  showChatFiles();
  messages.replaceChildren();
  scrollRegion.scrollTop = 0;
  el("empty").hidden = true;
  el("history-limit").hidden = true;
  el("chat-title").textContent = el("channel-list").querySelector('[data-channel="' + CSS.escape(channelId) + '"] span')?.textContent || t(channelId ? "channel" : "noChannelSelected");
  el("room-state").textContent = channelId ? t("loading") : "";
  showMessage("form-error", "");
  showSidePanel("channels", false);
  showChannelView();
  refreshControls();
  connectRoomStream();
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
recipient.addEventListener("change", () => {
  if (!recipientChoice.choose(recipient.value, current?.participants || [])) {
    if (current) showRecipients();
    return;
  }
  // A changed target is a new send intent, including drafts in other channels.
  for (const draft of drafts.values()) draft.requestId = null;
  chatDraftChanged();
});
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
  const target = recipientChoice.id;
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
      drafts.set(selectedChannel, { body: "", files: [], requestId: null });
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
el("empty-add-channel").addEventListener("click", () => editChannel());
el("channel-rename").addEventListener("click", () => {
  const channel = current?.channels.find(({ id }) => id === channelMenu.dataset.channel);
  closeChannelMenu(true);
  if (channel) editChannel(channel);
});
el("channel-delete").addEventListener("click", () => {
  const channel = current?.channels.find(({ id }) => id === channelMenu.dataset.channel);
  closeChannelMenu(true);
  if (!channel) return;
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
    if (activeChannel === channelId) switchChannel("");
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
  el("board-path").textContent = channel.board;
  for (const kind of ["shared", "logs", "board"]) {
    el(kind + "-link").href = "/files?" + new URLSearchParams({ channel: channel.id, kind });
  }
}

el("show-folders").addEventListener("click", () => {
  if (!current?.channel) return;
  el("folder-form").reset();
  showFolderPaths(current.channel);
  showMessage("folder-error", "");
  el("folders-feedback").textContent = "";
  foldersDialog.showModal();
  el("server-folder-settings").open = false;
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

function clearIconDraft() {
  if (iconDraft?.previewUrl) URL.revokeObjectURL(iconDraft.previewUrl);
  iconDraft = null;
  el("icon-file").value = "";
}

function refreshIconEditor() {
  if (!iconDraft) return;
  const choice = el("agent-icon").value;
  const value = choice === "image" ? iconDraft.icon : choice;
  const preview = choice === "image" ? iconDraft.previewUrl : "";
  el("icon-preview").replaceChildren(participantAvatar({ ...iconDraft, icon: value }, preview));
  el("icon-fields").disabled = iconSaving;
  el("icon-submit").disabled = iconSaving || (choice === "image" ? !iconDraft.file : choice === iconDraft.icon);
}

function initializeIconEditor(setup) {
  clearIconDraft();
  iconDraft = { ...setup, file: null, previewUrl: "" };
  const choices = [...setup.icon_options, "image"].map((value) => {
    const item = option(value, t(participantIconLabels[value]));
    item.disabled = value === "image" && !setup.icon.startsWith("image:");
    return item;
  });
  el("agent-icon").replaceChildren(...choices);
  el("agent-icon").value = setup.icon.startsWith("image:") ? "image" : setup.icon;
  el("icon-file").accept = setup.icon_upload_extensions.join(",");
  showMessage("icon-error", "");
  el("icon-feedback").textContent = "";
  refreshIconEditor();
}

el("agent-icon").addEventListener("change", () => {
  showMessage("icon-error", "");
  el("icon-feedback").textContent = "";
  refreshIconEditor();
});
el("icon-upload").addEventListener("click", () => el("icon-file").click());
el("icon-file").addEventListener("change", () => {
  const file = el("icon-file").files[0];
  el("icon-file").value = "";
  if (!iconDraft || !file || iconSaving) return;
  const extension = file.name.trim().slice(file.name.trim().lastIndexOf(".")).toLowerCase();
  const selected = selectUploads([], [file]);
  const error = !iconDraft.icon_upload_extensions.includes(extension) ? t("iconImageTypes") : selected.error;
  showMessage("icon-error", error);
  el("icon-feedback").textContent = "";
  if (error) return;
  if (iconDraft.previewUrl) URL.revokeObjectURL(iconDraft.previewUrl);
  iconDraft.file = selected.files[0];
  iconDraft.previewUrl = URL.createObjectURL(file);
  el("agent-icon").querySelector('[value="image"]').disabled = false;
  el("agent-icon").value = "image";
  refreshIconEditor();
});
el("icon-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!iconDraft || iconSaving || el("icon-submit").disabled) return;
  const draft = iconDraft;
  const choice = el("agent-icon").value;
  let uploads = [];
  iconSaving = true;
  showMessage("icon-error", "");
  el("icon-feedback").textContent = t("iconSaving");
  refreshIconEditor();
  try {
    if (choice === "image") uploads = await uploadFiles([draft.file]);
    const saved = await request("/api/participants/icon", { id: draft.id, channel_id: draft.channel_id, icon: choice, upload_id: uploads[0]?.upload_id });
    if (iconDraft === draft) {
      initializeIconEditor({ ...draft, ...saved });
      el("icon-feedback").textContent = t("iconSaved");
    }
    await loadRoom();
  } catch (error) {
    if (iconDraft === draft) {
      showMessage("icon-error", error.message);
      el("icon-feedback").textContent = "";
    }
  } finally {
    iconSaving = false;
    refreshIconEditor();
    await discardUploads(uploads);
  }
});
setupDialog.addEventListener("close", () => {
  clearIconDraft();
  setupRequest++;
  setupDialog.removeAttribute("aria-busy");
});

function setSetupPromptLoading() {
  setupDialog.setAttribute("aria-busy", "true");
  for (const button of setupDialog.querySelectorAll("[data-copy]")) button.disabled = true;
  for (const id of ["setup-prompt", "setup-install-prompt", "setup-config"]) el(id).textContent = "";
  for (const id of ["setup-feedback", "setup-install-feedback", "setup-config-feedback"]) el(id).textContent = "";
  el("setup-language").textContent = t("loading");
  showMessage("setup-prompts-error", "");
  el("setup-prompts-retry").hidden = true;
}

function renderSetupPrompts(setup, language) {
  const name = current?.participants.find((agent) => agent.id === setup.id)?.name || setup.name;
  el("setup-title").textContent = t("connectAgent", { name });
  el("setup-id").textContent = roleName[setup.role] + " · " + setup.id;
  el("setup-language").textContent = t("promptLanguage", { language: languageSelect.selectedOptions[0]?.textContent || language });
  el("setup-install").hidden = !setup.install_prompt;
  el("setup-install-prompt").textContent = setup.install_prompt || "";
  el("setup-install-instruction").textContent = t(setup.role === "codex" ? "codexInstall" : "claudeInstall");
  el("setup-instruction").textContent = setup.role === "codex"
    ? t("codexConfigHint") : t("claudeConfigHint", { path: setup.config_path || "~/.claude.json" });
  el("setup-config").textContent = setup.config;
  el("setup-prompt").textContent = setup.prompt;
  for (const id of ["setup-prompt", "setup-install-prompt", "setup-config"]) el(id).lang = language;
  for (const button of setupDialog.querySelectorAll("[data-copy]")) button.disabled = false;
  setupDialog.removeAttribute("aria-busy");
}

async function openSetup(agentId, refreshOnly = false) {
  if (refreshOnly && (!setupDialog.open || setupAgent !== agentId)) return;
  const requestId = ++setupRequest;
  const language = document.documentElement.lang;
  setSetupPromptLoading();
  const query = new URLSearchParams({ agent: agentId, lang: language, channel: activeChannel });
  let setup;
  try {
    setup = await request("/api/setup?" + query);
  } catch (error) {
    if (requestId !== setupRequest) return;
    setupDialog.removeAttribute("aria-busy");
    showMessage("setup-prompts-error", t("promptLoadFailed") + " " + error.message);
    el("setup-prompts-retry").hidden = false;
    if (!refreshOnly) throw error;
    return;
  }
  if (requestId !== setupRequest) return;
  if (refreshOnly && (!setupDialog.open || setupAgent !== agentId)) return;
  if (language !== document.documentElement.lang) return openSetup(agentId, refreshOnly);
  setupAgent = setup.id;
  if (!refreshOnly) {
    initializeIconEditor(setup);
    el("agent-name").value = setup.name;
    showMessage("rename-error", "");
    el("setup-install-preview").open = false;
    el("setup-manual-config").open = false;
  }
  renderSetupPrompts(setup, language);
  if (!setupDialog.open) setupDialog.showModal();
}

el("setup-prompts-retry").addEventListener("click", () => openSetup(setupAgent, true));

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
  const channelId = activeChannel;
  try {
    agent = await request("/api/participants", { role: el("join-role").value, name: el("join-name").value, channel_id: channelId });
  } catch (error) {
    showMessage("join-error", error.message);
  } finally {
    button.disabled = false;
  }
  if (!agent || activeChannel !== channelId) return;
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
    const agent = await request("/api/participants/rename", { id, name: el("agent-name").value, channel_id: activeChannel });
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
el("agent-delete-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = el("agent-delete-confirm");
  if (button.disabled) return;
  button.disabled = true;
  const id = agentDeleteDialog.dataset.agentId;
  showMessage("agent-delete-error", "");
  try {
    await request("/api/participants/delete", { id, channel_id: activeChannel });
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
    if (activeChannel) right = Math.max(limits.agents[0], Math.min(right, budget - left));
    left = Math.max(limits.channels[0], Math.min(left, budget - (activeChannel ? right : 0)));
    for (const [name, width, otherWidth] of [["channels", left, activeChannel ? right : 0], ["agents", right, left]]) {
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
  for (const item of el("agent-icon").options) item.textContent = t(participantIconLabels[item.value]);
  if (current) applyRoom(current, true);
  connection(live);
  showChatFiles();
  if (foldersDialog.open) {
    const channel = current?.channels.find((item) => item.id === foldersDialog.dataset.channel);
    if (channel) el("folders-channel").textContent = channelName(channel);
  }
  if (setupDialog.open && setupAgent) openSetup(setupAgent, true);
});

initializeLanguage().then(() => {
  document.title = appName + " — " + t("general");
  connectRoomStream();
  loadRoom();
});
