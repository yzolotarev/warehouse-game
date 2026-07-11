/* Тема панелей: auto (за системой) / light / dark. Хранится в localStorage.whTheme. */
(function () {
  "use strict";
  const mq = matchMedia("(prefers-color-scheme: dark)");
  const resolve = t => t === "auto" ? (mq.matches ? "dark" : "light") : t;
  const apply = () => {
    document.documentElement.dataset.theme = resolve(localStorage.whTheme || "auto");
  };
  apply();
  mq.addEventListener("change", apply);
  addEventListener("DOMContentLoaded", () => {
    const icons = { auto: "◐", light: "☀️", dark: "🌙" };
    const b = document.createElement("div");
    b.id = "themeTgl";
    b.title = "тема: авто → светлая → тёмная";
    b.textContent = icons[localStorage.whTheme || "auto"];
    b.onclick = () => {
      const cur = localStorage.whTheme || "auto";
      const next = cur === "auto" ? "light" : cur === "light" ? "dark" : "auto";
      localStorage.whTheme = next;
      b.textContent = icons[next];
      apply();
    };
    document.body.appendChild(b);
  });
})();
