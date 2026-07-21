"use strict";
/* Амбиенс-фон склада (#307, 21.07): приглушённое живое видео + звук вместо плоского
   фона. Психология (юзер): «я в хайлайте стрима → я не один → нет тревожности»;
   грустный пейзаж = как грустная музыка при грусти. Канал-фаворит: Stația Nouă.
   ЛОКАЛЬНЫЕ файлы - основной путь (ютуб-embed в RU = «вы не бот», похоронен 21.07):
   /ambient_list = содержимое assets/ambient/, кинул файл - появился в меню.
   Против лага на переходах: позиция запоминается (localStorage), видео продолжает
   С ТОГО ЖЕ МЕСТА и плавно проявляется из тёмной подложки, а не стартует с нуля.
   Режим «🎲 авто»: /ambient_pick выбирает фон по ситуации (правило + LLM сторожа);
   клиент НЕ ждёт ответа - играет прошлый выбор, свежий применится на след. заходе. */
(function () {
  if (location.pathname.startsWith("/capture")) return; // секундное окно захвата
  const LS = "wh_ambient", LSPOS = "wh_amb_pos", LSPICK = "wh_amb_pick";
  let st = { on: false, src: "auto", sound: false, custom: [] };
  try { st = Object.assign(st, JSON.parse(localStorage.getItem(LS) || "{}")); } catch (e) {}
  const save = () => localStorage.setItem(LS, JSON.stringify(st));
  let files = [], pick = null;
  try { pick = JSON.parse(localStorage.getItem(LSPICK) || "null"); } catch (e) {}

  const css = document.createElement("style");
  css.textContent = `
    #whAmbBtn { position:fixed; left:14px; bottom:86px; z-index:60; cursor:pointer;
      font-size:20px; opacity:.55; transition:opacity .2s; user-select:none; }
    #whAmbBtn:hover { opacity:1; }
    #whAmbPanel { position:fixed; left:14px; bottom:120px; z-index:61; display:none;
      background:var(--card,#222229); border:1px solid var(--line,#3a3a44);
      border-radius:12px; padding:12px; width:240px; font-size:13px;
      color:var(--txt,#eee); box-shadow:0 6px 24px rgba(0,0,0,.35); }
    #whAmbPanel .amb-row { display:block; width:100%; text-align:left; margin:3px 0;
      padding:6px 8px; border:0; border-radius:8px; background:transparent;
      color:inherit; cursor:pointer; font:inherit; }
    #whAmbPanel .amb-row:hover { background:var(--line,#3a3a44); }
    #whAmbPanel .amb-row.act { background:var(--line,#3a3a44); }
    #whAmbPanel input { width:100%; box-sizing:border-box; margin-top:6px;
      background:transparent; border:1px solid var(--line,#3a3a44); border-radius:8px;
      padding:5px 8px; color:inherit; font:inherit; font-size:12px; }
    /* тёмная подложка: пока видео грузится или упало - фон просто тёмный */
    #whAmbLayer { position:fixed; inset:0; z-index:-1; overflow:hidden; background:#101014; }
    #whAmbLayer video, #whAmbLayer iframe { position:absolute; top:50%; left:50%;
      width:177.8vh; height:56.25vw; min-width:100vw; min-height:100vh;
      transform:translate(-50%,-50%); filter:saturate(.4) brightness(.55);
      pointer-events:none; border:0; object-fit:cover;
      opacity:0; transition:opacity .7s ease; }
    #whAmbLayer .ready { opacity:1; }
  `;
  document.head.appendChild(css);

  const btn = document.createElement("div");
  btn.id = "whAmbBtn"; btn.title = "Фон склада";
  const panel = document.createElement("div");
  panel.id = "whAmbPanel";
  btn.onclick = e => { e.stopPropagation(); togglePanel(); };
  function togglePanel(force) {
    panel.style.display = force !== undefined ? (force ? "block" : "none")
      : (panel.style.display === "block" ? "none" : "block");
  }
  // сворачивание: клик мимо панели или Esc - панель прячется
  document.addEventListener("click", e => {
    if (panel.style.display === "block" && !panel.contains(e.target)) togglePanel(false);
  });
  document.addEventListener("keydown", e => { if (e.key === "Escape") togglePanel(false); });

  document.addEventListener("DOMContentLoaded", async () => {
    document.body.appendChild(btn); document.body.appendChild(panel);
    if (st.on) mount(); // не ждём сеть: играем прошлый выбор сразу
    try { files = (await (await fetch("/ambient_list")).json()).files || []; } catch (e) {}
    render();
    if (st.on && st.src === "auto") refreshPick();
  });

  async function refreshPick() {
    try {
      const p = await (await fetch("/ambient_pick?page=" +
        encodeURIComponent(location.pathname.replace(/^\//, "")))).json();
      if (p && p.file) {
        const stale = !pick || pick.file !== p.file;
        pick = p; localStorage.setItem(LSPICK, JSON.stringify(p));
        btn.title = "Фон склада · авто: " + (p.reason || p.file);
        if (stale) { mount(); render(); }
      }
    } catch (e) {}
  }

  function render() {
    btn.textContent = st.on ? "🌦" : "🌫";
    const rows =
      row("auto", "🎲 авто (по ситуации)") +
      files.map(f => row("f:" + f, f.replace(/\.[^.]+$/, ""))).join("") +
      (st.custom || []).map(c => row("y:" + c.id, c.name)).join("");
    panel.innerHTML = `
      <button class="amb-row" data-a="off">${st.on ? "⬛ Выключить фон" : "▶ Включить фон"}</button>
      ${rows}
      <button class="amb-row" data-a="snd">${st.sound ? "🔊 звук вкл" : "🔇 звук выкл"}</button>
      <input id="whAmbAdd" placeholder="вставь ссылку YouTube…">`;
    panel.querySelectorAll(".amb-row").forEach(b => b.onclick = e => { e.stopPropagation(); act(b); });
    const inp = panel.querySelector("#whAmbAdd");
    inp.onkeydown = e => { if (e.key === "Enter") addCustom(inp.value); };
  }
  function row(src, name) {
    return `<button class="amb-row${st.on && st.src === src ? " act" : ""}" data-s="${src}">${name}</button>`;
  }

  function act(b) {
    if (b.dataset.s) {
      st.src = b.dataset.s; st.on = true;
      togglePanel(false); // выбрал фон - панель свернулась
      if (st.src === "auto") refreshPick();
    }
    else if (b.dataset.a === "off") st.on = !st.on;
    else if (b.dataset.a === "snd") st.sound = !st.sound;
    save(); mount(); render();
  }
  function addCustom(url) {
    const m = (url || "").match(/(?:v=|youtu\.be\/|embed\/)([\w-]{11})/);
    if (!m) return;
    st.custom = (st.custom || []).concat([{ id: m[1], name: "🎞 " + m[1] }]);
    st.src = "y:" + m[1]; st.on = true; save(); togglePanel(false); mount(); render();
  }

  function curSrc() {
    if (st.src === "auto") return pick && pick.file ? "f:" + pick.file : (files.length ? "f:" + files[0] : "");
    return st.src;
  }
  function posLoad() { try { return JSON.parse(localStorage.getItem(LSPOS) || "{}"); } catch (e) { return {}; } }

  function mount() {
    let layer = document.getElementById("whAmbLayer");
    if (!st.on) { if (layer) layer.remove(); restoreBg(); return; }
    document.documentElement.style.background = "transparent";
    document.body.style.background = "transparent";
    if (!layer) {
      layer = document.createElement("div"); layer.id = "whAmbLayer";
      document.body.prepend(layer);
    }
    const src = curSrc();
    if (!src) return;
    let html = "";
    if (src.startsWith("f:")) {
      const name = src.slice(2);
      html = `<video data-k="${src}" src="/assets/ambient/${encodeURIComponent(name)}" `
        + `autoplay loop playsinline preload="auto" ${st.sound ? "" : "muted"}></video>`;
    } else if (src.startsWith("y:")) {
      const id = src.slice(2);
      const p = `autoplay=1&mute=${st.sound ? 0 : 1}&loop=1&playlist=${id}&controls=0&rel=0&iv_load_policy=3`;
      html = `<iframe class="ready" data-k="${src}:${st.sound}" `
        + `src="https://www.youtube-nocookie.com/embed/${id}?${p}" allow="autoplay"></iframe>`;
    }
    const key = src.startsWith("f:") ? src : src + ":" + st.sound;
    const cur = layer.firstElementChild;
    if (!cur || cur.dataset.k !== key) layer.innerHTML = html;
    const v = layer.querySelector("video");
    if (v) {
      v.muted = !st.sound;
      const name = src.slice(2);
      if (!v.dataset.resumed) {
        v.dataset.resumed = "1";
        const t = posLoad()[name] || 0;
        const seek = () => { try { if (t > 1 && t < v.duration - 2) v.currentTime = t; } catch (e) {} };
        (v.readyState >= 1) ? seek() : v.addEventListener("loadedmetadata", seek, { once: true });
        v.addEventListener("playing", () => v.classList.add("ready"));
        let last = 0;
        v.addEventListener("timeupdate", () => { // позиция раз в 3с - переходы продолжают с места
          if (v.currentTime - last > 3 || v.currentTime < last) {
            last = v.currentTime;
            const pos = posLoad(); pos[name] = Math.floor(v.currentTime);
            localStorage.setItem(LSPOS, JSON.stringify(pos));
          }
        });
      }
      v.play().catch(() => {});
    }
  }
  function restoreBg() {
    document.documentElement.style.background = "";
    document.body.style.background = "";
  }
})();
