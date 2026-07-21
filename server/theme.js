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

/* Долгое отсутствие -> назад к меню (constrained front door): если окно было
   скрыто (свёрнуто/переключено) 5+ минут, при возврате не возобновляем
   случайную подстраницу, где всё было брошено, а кидаем на /hub -
   единую точку "что сейчас". */
(function () {
  "use strict";
  const AWAY_MS = 5 * 60 * 1000;
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      localStorage.whHiddenAt = Date.now();
      return;
    }
    const since = Number(localStorage.whHiddenAt || 0);
    delete localStorage.whHiddenAt;
    if (since && Date.now() - since >= AWAY_MS && location.pathname !== "/hub") {
      location.replace("/hub");
    }
  });
})();

/* Отметка активности: клик/клавиша где угодно в приложении обновляют
   whLastActive - "когда в последний раз реально работал со складом".
   Читает это hub.html при своей загрузке, ДО этой отметки, чтобы посчитать
   простой - поэтому здесь только по интерактивным событиям, не по факту
   загрузки страницы (иначе сам заход на hub стирал бы простой раньше проверки). */
(function () {
  "use strict";
  let last = 0;
  const mark = () => {
    const now = Date.now();
    if (now - last < 10000) return; // не долбить localStorage на каждый клик
    last = now;
    localStorage.whLastActive = now;
  };
  document.addEventListener("click", mark);
  document.addEventListener("keydown", mark);
})();

/* Нижняя шкала: заполнение + цвет (зелёный->янтарь->красный), без цифр
   (число только в title по hover). По умолчанию - "загрузка дня" 7:00-23:00,
   НО если страница сама двигает конкретное дело (разбор инбокса, шаги проекта),
   она вызывает WH_setLoadBar(pct, title) и ставит WH_LOADBAR_OWNED=true один раз -
   тогда часы дня замолкают, и шкала целиком принадлежит действию на этой
   странице (продвигается по факту сделанного, не по факту "время идёт"). */
(function () {
  "use strict";
  const START = 7, END = 23;
  const dayPct = () => {
    const d = new Date(), h = d.getHours() + d.getMinutes() / 60;
    if (h <= START) return 0;
    if (h >= END) return 100;
    return Math.round((h - START) / (END - START) * 100);
  };
  const hex2rgb = h => {
    h = h.replace("#", "").trim();
    if (h.length === 3) h = [...h].map(c => c + c).join("");
    const n = parseInt(h, 16);
    return [n >> 16 & 255, n >> 8 & 255, n & 255];
  };
  const mix = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));
  const scaleColor = p => {
    const cs = getComputedStyle(document.documentElement);
    const g = hex2rgb(cs.getPropertyValue("--ok"));
    const a = hex2rgb(cs.getPropertyValue("--accent"));
    const r = hex2rgb(cs.getPropertyValue("--danger"));
    const [c1, c2, t] = p < 50 ? [g, a, p / 50] : [a, r, (p - 50) / 50];
    const m = mix(c1, c2, t);
    return `rgb(${m[0]},${m[1]},${m[2]})`;
  };
  window.WH_setLoadBar = (p, title) => {
    p = Math.max(0, Math.min(100, p));
    const bar = document.getElementById("dayload");
    const lbl = document.getElementById("dayloadLbl");
    if (bar) {
      const fill = bar.firstElementChild;
      fill.style.width = p + "%";
      fill.style.background = scaleColor(p);
    }
    if (lbl && title) lbl.title = title;
  };
  const tick = () => { if (!window.WH_LOADBAR_OWNED) window.WH_setLoadBar(dayPct(), "день загружен: " + dayPct() + "%"); };
  addEventListener("DOMContentLoaded", () => {
    const bar = document.createElement("div");
    bar.id = "dayload";
    bar.innerHTML = "<i></i>";
    document.body.appendChild(bar);
    const lbl = document.createElement("div");
    lbl.id = "dayloadLbl";
    lbl.textContent = "⏳";
    document.body.appendChild(lbl);
    tick();
  });
  setInterval(tick, 60000);
})();
