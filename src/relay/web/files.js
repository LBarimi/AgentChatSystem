function fileView(search) {
  const query = new URLSearchParams(search);
  const offset = Number(query.get("offset"));
  return {
    channel: query.get("channel") || "general",
    kind: query.get("kind") || "shared",
    path: (query.get("path") || "").replaceAll("\\", "/"),
    offset: Number.isSafeInteger(offset) && offset >= 0 ? offset : 0,
  };
}

function folderURL(view, path = view.path, offset = 0, kind = view.kind) {
  const query = new URLSearchParams({ channel: view.channel, kind });
  if (path) query.set("path", path);
  if (offset) query.set("offset", offset);
  return "/files?" + query;
}

function fileCrumbs(view) {
  const crumbs = [{ name: view.kind + "/", path: "" }];
  const parts = view.path ? view.path.split("/") : [];
  parts.forEach((name, index) => crumbs.push({ name: name + "/", path: parts.slice(0, index + 1).join("/") }));
  return crumbs;
}

function fileSize(bytes, language) {
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  const unit = bytes > 0 ? Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1) : 0;
  return new Intl.NumberFormat(language, { maximumFractionDigits: unit ? 1 : 0 }).format(bytes / 1024 ** unit) + " " + units[unit];
}

function initializeFileBrowser() {
  const el = (id) => document.getElementById(id);
  const view = fileView(location.search);
  let requestId = 0;
  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }
  function fileIcon(folder) {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", "icon file-symbol");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", folder ? "M3 6h7l2 3h9v11H3z M3 6V4h7l2 2h9v3" : "M5 3h9l5 5v13H5z M14 3v6h5 M9 13h6 M9 17h6");
    svg.append(path);
    return svg;
  }
  function navigation() {
    for (const kind of ["shared", "logs", "board"]) {
      const link = el("files-" + kind);
      link.href = folderURL(view, "", 0, kind);
      if (view.kind === kind) link.setAttribute("aria-current", "page");
    }
    el("files-breadcrumbs").replaceChildren(...fileCrumbs(view).map((crumb, index, all) => {
      const link = node("a", "", crumb.name);
      link.href = folderURL(view, crumb.path);
      if (index === all.length - 1) link.setAttribute("aria-current", "location");
      return link;
    }));
    el("files-up").hidden = !view.path;
    el("files-up").href = folderURL(view, view.path.split("/").slice(0, -1).join("/"));
    el("files-previous").href = folderURL(view, view.path, Math.max(0, view.offset - 200));
    el("files-next").href = folderURL(view, view.path, view.offset + 200);
  }
  async function loadFiles() {
    const currentRequest = ++requestId;
    navigation();
    el("files-entries").replaceChildren();
    el("files-table").setAttribute("aria-busy", "true");
    el("files-status").textContent = t("loading");
    el("files-error").hidden = true;
    el("files-empty").hidden = true;
    el("files-previous").hidden = true;
    el("files-next").hidden = true;
    try {
      const [result, channels] = await Promise.all([
        request("/api/files?" + new URLSearchParams(view)), request("/api/channels"),
      ]);
      if (currentRequest !== requestId) return;
      const channel = channels.channels.find((channel) => channel.id === view.channel);
      const name = channel?.default_name ? t("general") : channel?.name || view.channel;
      el("files-channel").textContent = name;
      document.title = `${appName} — ${name} · ${view.kind}/${view.path}`;
      for (const entry of result.entries) {
        const folder = entry.type === "folder";
        const name = entry.path.split("/").at(-1);
        const row = node("tr");
        const nameCell = node("td");
        const link = node("a", "files-name");
        link.append(fileIcon(folder), node("span", "", name + (folder ? "/" : "")));
        link.href = folder ? folderURL(view, entry.path)
          : "/api/files/download?" + new URLSearchParams({ channel: view.channel, kind: view.kind, path: entry.path });
        if (!folder) {
          link.download = name;
          link.setAttribute("aria-label", t("downloadFile", { name }));
        }
        nameCell.append(link);
        const size = node("td", "files-size", folder ? "—" : fileSize(entry.bytes, document.documentElement.lang));
        const action = node("td", "files-action");
        if (folder) {
          action.append(node("span", "files-folder-label", t("fileFolder")));
        } else {
          const download = node("a", "button secondary", t("download"));
          download.href = link.href;
          download.download = name;
          download.setAttribute("aria-label", t("downloadFile", { name }));
          action.append(download);
        }
        row.append(nameCell, size, action);
        el("files-entries").append(row);
      }
      el("files-empty").hidden = result.entries.length > 0;
      el("files-status").textContent = t("filesPage", { page: Math.floor(result.offset / 200) + 1 });
      el("files-previous").hidden = result.offset === 0;
      el("files-next").hidden = !result.truncated;
    } catch (error) {
      if (currentRequest !== requestId) return;
      el("files-channel").textContent = view.channel;
      el("files-status").textContent = "";
      el("files-error").textContent = error.message;
      el("files-error").hidden = false;
    } finally {
      if (currentRequest === requestId) el("files-table").removeAttribute("aria-busy");
    }
  }
  el("files-refresh").addEventListener("click", loadFiles);
  document.addEventListener("languagechange", loadFiles);
  initializeLanguage().then(loadFiles).catch((error) => {
    el("files-status").textContent = "";
    el("files-error").textContent = error.message;
    el("files-error").hidden = false;
    el("files-table").removeAttribute("aria-busy");
  });
}

if (typeof document !== "undefined") initializeFileBrowser();
