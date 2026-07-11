/* Тема панелей: auto (за системой) / light / dark. Хранится в localStorage.whTheme. */
(function () {
  "use strict";
  // auto = по времени суток: день (08-20) светлая бумага, вечер/ночь - тёмная
  const byClock = () => { const h = new Date().getHours(); return h >= 8 && h < 20 ? "light" : "dark"; };
  const resolve = t => t === "auto" ? byClock() : t;
  const apply = () => {
    document.documentElement.dataset.theme = resolve(localStorage.whTheme || "auto");
  };
  apply();
  setInterval(apply, 60000);
  addEventListener("storage", apply);
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
