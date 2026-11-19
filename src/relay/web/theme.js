(() => {
  const theme = document.getElementById("theme");
  const systemTheme = matchMedia("(prefers-color-scheme: dark)");
  function readTheme() {
    try { theme.value = localStorage.getItem("relay-theme") || "system"; }
    catch { theme.value = "system"; }
    if (!theme.value) theme.value = "system";
  }
  function applyTheme() {
    document.documentElement.dataset.theme = theme.value === "system"
      ? (systemTheme.matches ? "dark" : "light") : theme.value;
    document.querySelector('meta[name="theme-color"]').content = getComputedStyle(document.documentElement).getPropertyValue("--cool").trim();
  }
  readTheme();
  applyTheme();
  theme.addEventListener("change", () => {
    applyTheme();
    try { localStorage.setItem("relay-theme", theme.value); } catch { /* Keep the current page usable without storage. */ }
  });
  systemTheme.addEventListener("change", applyTheme);
  window.addEventListener("storage", (event) => {
    if (event.key === "relay-theme" || event.key === null) { readTheme(); applyTheme(); }
  });
})();
