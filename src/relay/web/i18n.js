const appName = "AgentChatSystem";
const languageSelect = document.getElementById("language");
const supportedLanguages = new Set([...languageSelect.options].map(({ value }) => value));
const languageError = document.getElementById("language-error");
let translations = {};
let dateFormat;

function t(key, values = {}) {
  return (translations[key] ?? key).replace(/\{(\w+)\}/g, (_, name) => values[name] ?? "");
}

function draftId() {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  // LAN HTTP is not a secure context; getRandomValues still supplies random bytes.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function browserLanguage(preferences) {
  for (const preference of preferences) {
    const [language, ...subtags] = preference.toLowerCase().split("-");
    if (language === "zh") {
      const script = subtags.find((tag) => tag === "hant" || tag === "hans");
      return script === "hant" || (!script && subtags.some((tag) => ["tw", "hk", "mo"].includes(tag)))
        ? "zh-TW" : "zh-CN";
    }
    if (supportedLanguages.has(language)) return language;
  }
  return "en";
}

function apiErrorMessage(result) {
  return Object.hasOwn(translations, result.error_key)
    ? t(result.error_key, result.error_values) : result.error || t("requestFailed");
}

async function request(route, payload) {
  const response = await fetch(route, payload === undefined ? undefined : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Local-Relay": "1" },
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(apiErrorMessage(result));
    error.status = response.status;
    error.key = result.error_key;
    throw error;
  }
  return result;
}

function applyLanguage(language, catalog) {
  translations = catalog;
  document.documentElement.lang = language;
  dateFormat = new Intl.DateTimeFormat(language, { month: "long", day: "numeric", hour: "2-digit", minute: "2-digit" });
  for (const node of document.querySelectorAll("[data-i18n]")) {
    node.textContent = t(node.dataset.i18n);
  }
  for (const attribute of ["aria-label", "placeholder", "title"]) {
    const marker = "data-i18n-" + attribute;
    for (const node of document.querySelectorAll(`[${marker}]`)) {
      node.setAttribute(attribute, t(node.getAttribute(marker)));
    }
  }
  languageSelect.value = language;
}

async function initializeLanguage() {
  let language = browserLanguage(navigator.languages?.length ? navigator.languages : [navigator.language || "en"]);
  let settingsFailed = false;
  try {
    const settings = await request("/api/settings");
    if (supportedLanguages.has(settings.language)) {
      language = settings.language;
    } else {
      const saved = await request("/api/settings/language", { language, initialize: true });
      language = saved.language;
    }
  } catch {
    settingsFailed = true;
  }
  try {
    applyLanguage(language, await request(`/locales/${language}.json`));
  } catch {
    applyLanguage("en", await request("/locales/en.json"));
  } finally {
    languageSelect.disabled = false;
  }
  if (settingsFailed) showLanguageError();
}

function showLanguageError() {
  languageError.textContent = t("languageSaveFailed");
  languageError.hidden = false;
}

languageSelect.addEventListener("change", async () => {
  const language = languageSelect.value;
  const previous = document.documentElement.lang;
  languageSelect.disabled = true;
  languageError.hidden = true;
  try {
    const catalog = await request(`/locales/${language}.json`);
    await request("/api/settings/language", { language });
    applyLanguage(language, catalog);
    document.dispatchEvent(new Event("languagechange"));
  } catch {
    languageSelect.value = previous;
    showLanguageError();
  } finally {
    languageSelect.disabled = false;
  }
});
