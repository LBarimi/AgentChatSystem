// The server generates uploadPolicy from relay.uploads for both composers.
for (const id of ["chat-file-input", "board-file"]) {
  document.getElementById(id).accept = uploadPolicy.extensions.join(",");
}

function selectUploads(currentFiles, incoming) {
  const files = [...currentFiles];
  let error = "";
  for (const file of incoming) {
    if (files.length >= uploadPolicy.max_files) {
      error = t("attachmentLimit");
      break;
    }
    const name = file.name.trim();
    const extension = name.slice(name.lastIndexOf(".")).toLowerCase();
    if (!uploadPolicy.extensions.includes(extension)) {
      error = t("attachmentTypes");
      continue;
    }
    if (file.size > uploadPolicy.max_file_bytes) {
      error = t("attachmentSizeLimit");
      continue;
    }
    files.push({ file, name });
  }
  return { files, error };
}

async function discardUploads(attachments) {
  if (!attachments?.length) return;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5000);
  try {
    await fetch("/api/uploads/discard", {
      method: "POST", signal: controller.signal,
      headers: { "Content-Type": "application/json", "X-Local-Relay": "1" },
      body: JSON.stringify({ attachments }),
    });
  } catch {
    // An interrupted upload also expires on the server after one hour.
  } finally {
    clearTimeout(timer);
  }
}

async function uploadFiles(files, onProgress) {
  const attachments = [];
  try {
    for (const { file, name } of files) {
      onProgress?.(attachments.length + 1, files.length);
      const response = await fetch("/api/uploads?" + new URLSearchParams({ name }), {
        method: "POST", body: file,
        headers: { "Content-Type": "application/octet-stream", "X-Local-Relay": "1" },
      });
      const result = await response.json();
      if (!response.ok) throw new Error(apiErrorMessage(result));
      attachments.push(result);
    }
    return attachments;
  } catch (error) {
    await discardUploads(attachments);
    throw error;
  }
}

function attachmentPreview(file, address) {
  if (file.mime_type.startsWith("image/")) {
    const link = element("a", "attachment-preview");
    link.href = address;
    link.target = "_blank";
    link.rel = "noopener";
    const image = element("img");
    image.src = address;
    image.alt = file.name;
    image.loading = "lazy";
    link.append(image);
    return link;
  }
  if (file.mime_type === "video/mp4") {
    const video = element("video", "attachment-video");
    video.src = address;
    video.controls = true;
    video.preload = "metadata";
    video.setAttribute("aria-label", file.name);
    return video;
  }
  return null;
}
