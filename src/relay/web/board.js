const boardView = el("board-view");
const boardDialog = el("board-write-dialog");
const boardDeleteDialog = el("board-delete-dialog");
const boardForm = el("board-form");
const boardFile = el("board-file");
const boardList = el("board-list");
const boardPost = el("board-post");
const maxBoardImageBytes = 4 * 1024 * 1024;
const boardFiles = [];
const boardPages = [];
let boardRequest = 0;
let boardSelected = null;
let boardNext = null;
let boardRenderKey = "";
let boardPosting = false;
let boardDraftId = null;

function showBoardHeading() {
  el("board-toggle-label").textContent = t("viewChat");
  el("chat-title").textContent = t("board");
  el("room-state").textContent = t("boardSubtitle");
  document.title = appName + " — " + t("board");
}

function showBoard(open) {
  boardOpen = open;
  boardView.hidden = !open;
  scrollRegion.hidden = open;
  el("composer").parentElement.hidden = open;
  el("show-folders").hidden = open;
  el("clear-chat").hidden = open;
  const toggle = el("board-toggle");
  toggle.setAttribute("aria-pressed", String(open));
  if (open) {
    showBoardHeading();
    loadBoard();
  } else {
    el("board-toggle-label").textContent = t("board");
    boardRequest += 1;
    showChatHeading();
  }
  el("chat-title").focus({ preventScroll: true });
}

function boardDate(value) {
  const date = element("time", "", dateFormat.format(new Date(value)));
  date.dateTime = value;
  return date;
}

function boardCard(post) {
  const item = element("li");
  const card = element("button", "board-card");
  card.type = "button";
  card.dataset.postId = post.id;
  card.append(element("strong", "", post.title));
  if (post.excerpt) card.append(element("span", "board-excerpt", post.excerpt));
  const meta = element("span", "board-meta");
  meta.append(boardDate(post.created_at));
  if (post.image_count) meta.append(element("span", "", t("imageCount", { count: post.image_count })));
  meta.append(element("span", "", t("agentReads", { read: post.read_count, total: post.recipient_count })));
  card.append(meta);
  item.append(card);
  return item;
}

function renderBoardPost(post) {
  const wasExpanded = boardPost.querySelector("details")?.open || false;
  const heading = element("h2", "", post.title);
  const stamp = element("div", "board-meta");
  stamp.append(boardDate(post.created_at));
  const nodes = [heading, stamp];
  if (post.body) nodes.push(element("p", "board-post-body", post.body));
  if (post.images.length) {
    const gallery = element("div", "board-gallery");
    for (const image of post.images) {
      const figure = element("figure");
      const original = element("a");
      original.href = "/api/board/image?id=" + image.id;
      original.target = "_blank";
      original.rel = "noopener";
      const preview = element("img");
      preview.src = original.href;
      preview.alt = image.name;
      preview.loading = "lazy";
      original.append(preview);
      figure.append(original, element("figcaption", "", image.name));
      gallery.append(figure);
    }
    nodes.push(gallery);
  }
  const readCount = post.recipients.filter((agent) => agent.read_at).length;
  const reading = element("details", "board-reading");
  reading.open = wasExpanded;
  reading.append(element("summary", "", t("agentReads", { read: readCount, total: post.recipients.length })));
  const receipts = element("ul", "board-receipts");
  for (const agent of post.recipients) {
    const row = element("li");
    row.append(element("span", "", agent.name), element("span", "", agent.read_at ? t("read") : t("unread")));
    receipts.append(row);
  }
  reading.append(receipts);
  boardPost.replaceChildren(...nodes, reading);
}

async function loadBoard() {
  if (!boardOpen) return;
  const sequence = ++boardRequest;
  const before = boardPages.at(-1);
  const route = boardSelected === null
    ? "/api/board" + (before ? "?before=" + before : "")
    : "/api/board/post?id=" + boardSelected;
  try {
    const result = await request(route);
    if (sequence !== boardRequest || !boardOpen) return;
    const key = JSON.stringify([route, result]);
    if (key !== boardRenderKey) {
      if (boardSelected === null) {
        boardList.replaceChildren(...result.posts.map(boardCard));
        boardNext = result.next_before;
      } else {
        renderBoardPost(result);
      }
      boardRenderKey = key;
    }
    const listMode = boardSelected === null;
    showMessage("board-status", listMode && !result.posts.length ? t("noPosts") : "");
    boardList.hidden = !listMode;
    boardPost.hidden = listMode;
    el("board-back").hidden = listMode;
    el("board-delete").hidden = listMode;
    el("board-pages").hidden = !listMode || (!boardPages.length && !boardNext);
    el("board-newer").hidden = !boardPages.length;
    el("board-older").hidden = !boardNext;
  } catch (error) {
    if (sequence !== boardRequest) return;
    if (boardSelected !== null && (error.status === 404 || error.status === 410)) {
      showBoardPage(null);
    } else {
      showMessage("board-status", error.message);
    }
  }
}

function showBoardPage(postId) {
  boardSelected = postId;
  boardRenderKey = "";
  boardList.hidden = true;
  boardPost.hidden = true;
  el("board-back").hidden = postId === null;
  el("board-delete").hidden = true;
  el("board-pages").hidden = true;
  boardView.scrollTop = 0;
  showMessage("board-status", t("loading"));
  loadBoard();
}

el("board-toggle").addEventListener("click", () => showBoard(!boardOpen));
el("board-back").addEventListener("click", () => showBoardPage(null));
el("board-delete").addEventListener("click", () => {
  if (boardSelected === null || boardPost.hidden) return;
  boardDeleteDialog.dataset.postId = boardSelected;
  el("board-delete-name").textContent = boardPost.querySelector("h2").textContent;
  showMessage("board-delete-error", "");
  boardDeleteDialog.showModal();
});
el("board-delete-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = el("board-delete-confirm");
  if (button.disabled) return;
  button.disabled = true;
  const postId = Number(boardDeleteDialog.dataset.postId);
  showMessage("board-delete-error", "");
  try {
    await request("/api/board/delete", { post_id: postId });
    if (Number(boardDeleteDialog.dataset.postId) === postId) boardDeleteDialog.close();
    if (boardSelected === postId) showBoardPage(null);
  } catch (error) {
    if (Number(boardDeleteDialog.dataset.postId) === postId) showMessage("board-delete-error", error.message);
  } finally {
    button.disabled = false;
  }
});
el("board-newer").addEventListener("click", () => {
  boardPages.pop();
  showBoardPage(null);
});
el("board-older").addEventListener("click", () => {
  if (boardNext) {
    boardPages.push(boardNext);
    showBoardPage(null);
  }
});
boardList.addEventListener("click", (event) => {
  const card = event.target.closest("[data-post-id]");
  if (card) showBoardPage(Number(card.dataset.postId));
});

function showBoardFiles() {
  const rows = boardFiles.map((file, index) => {
    const row = element("li");
    const remove = element("button", "button icon-button");
    remove.type = "button";
    remove.setAttribute("aria-label", t("removeAttachment", { name: file.name }));
    remove.append(icon("x"));
    remove.addEventListener("click", () => {
      if (boardPosting) return;
      boardFiles.splice(index, 1);
      boardDraftId = null;
      showBoardFiles();
    });
    row.append(element("span", "", file.name), remove);
    return row;
  });
  el("board-files").replaceChildren(...rows);
}

document.addEventListener("languagechange", () => {
  if (boardOpen) {
    showBoardHeading();
    boardRenderKey = "";
    loadBoard();
  }
  showBoardFiles();
});

function attachBoardFiles(incoming) {
  if (boardPosting || !incoming.length) return;
  showMessage("board-form-error", "");
  for (const file of incoming) {
    if (boardFiles.length >= 4) {
      showMessage("board-form-error", t("boardImageLimit"));
      break;
    }
    const supported = /^(image\/(png|jpeg|webp))$/.test(file.type) || /\.(png|jpe?g|webp)$/i.test(file.name);
    if (!file.size || file.size > maxBoardImageBytes || !supported) {
      showMessage("board-form-error", t("boardImageTypes"));
      continue;
    }
    boardDraftId = null;
    boardFiles.push(file);
  }
  showBoardFiles();
}

el("board-write").addEventListener("click", () => {
  showMessage("board-form-error", "");
  boardDialog.showModal();
  el("board-title-input").focus();
});
boardForm.addEventListener("input", (event) => {
  if (event.target !== boardFile) boardDraftId = null;
});
boardFile.addEventListener("change", () => {
  attachBoardFiles(Array.from(boardFile.files));
  boardFile.value = "";
});
boardDialog.addEventListener("paste", (event) => {
  const pictures = Array.from(event.clipboardData?.files || []).filter((file) => file.type.startsWith("image/"));
  if (pictures.length) {
    event.preventDefault();
    attachBoardFiles(pictures);
  }
});

boardForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (boardPosting) return;
  const title = el("board-title-input").value.trim();
  const body = el("board-body-input").value.trim();
  if (!title || (!body && !boardFiles.length)) {
    showMessage("board-form-error", t("boardRequired"));
    return;
  }
  boardPosting = true;
  el("board-publish").disabled = true;
  showMessage("board-form-error", "");
  boardDraftId ||= draftId();
  const requestId = boardDraftId;
  try {
    const images = await Promise.all(boardFiles.map((file) => readUpload(file, file.name || t("attachedImage"))));
    const post = await request("/api/board", { request_id: requestId, title, body, images });
    if (boardDraftId === requestId) {
      boardDialog.close();
      boardDraftId = null;
      boardForm.reset();
      boardFiles.length = 0;
      showBoardFiles();
    }
    boardPages.length = 0;
    showBoardPage(post.id);
  } catch (error) {
    showMessage("board-form-error", error.message);
  } finally {
    boardPosting = false;
    el("board-publish").disabled = false;
  }
});
