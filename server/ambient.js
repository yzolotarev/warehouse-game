"use strict";
/* Амбиенс-фон склада (#307, 21.07): вместо плоского фона - приглушённое живое видео
   (пейзажи Stația Nouă и т.п.) + звук. Шум переезжает ВНУТРЬ склада: знакомый фон
   вместо вкладки с ютубом. Психология (юзер, 21.07): «я в хайлайте стрима →
   я не один → нет тревожности»; грустный пейзаж работает как грустная музыка.
   ЛОКАЛЬНЫЕ файлы - основной путь (ютуб-embed в RU упирается в «вы не бот»):
   сервер отдаёт /ambient_list = содержимое assets/ambient/, кинул файл - появился.
   Ютуб остаётся только для ручных ссылок (вдруг заработает). Состояние в localStorage. */
(function () {
  if (location.pathname.startsWith("/capture")) return; // секундное окно захвата
  const LS = "wh_ambient";
  let st = { on: false, src: "", sound: false, custom: [] };
  try { st = Object.assign(st, JSON.parse(localStorage.getItem(LS) || "{}")); } catch (e) {}
  const save = () => localStorage.setItem(LS, JSON.stringify(st));
  let files = []; // с сервера

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
    /* тёмная подложка: видео не загрузилось - фон просто тёмный, без мусора */
    #whAmbLayer { position:fixed; inset:0; z-index:-1; overflow:hidden; background:#101014; }
    #whAmbLayer video, #whAmbLayer iframe { position:absolute; top:50%; left:50%;
      width:177.8vh; height:56.25vw; min-width:100vw; min-height:100vh;
      transform:translate(-50%,-50%); filter:saturate(.35) brightness(.55);
      pointer-events:none; border:0; object-fit:cover; }
  `;
  document.head.appendChild(css);

  const btn = document.createElement("div");
  btn.id = "whAmbBtn"; btn.title = "Фон склада";
  const panel = document.createElement("div");
  panel.id = "whAmbPanel";
  btn.onclick = () => { panel.style.display = panel.style.display === "block" ? "none" : "block"; };

  document.addEventListener("DOMContentLoaded", async () => {
    document.body.appendChild(btn); document.body.appendChild(panel);
    try { files = (await (await fetch("/ambient_list")).json()).files || []; } catch (e) {}
    if (st.on) mount();
    render();
  });

  function render() {
    btn.textContent = st.on ? "🌦" : "🌫";
    const rows =
      files.map(f => row("f:" + f, f.replace(/\.[^.]+$/, ""))).join("") +
      (st.custom || []).map(c => row("y:" + c.id, c.name)).join("");
    panel.innerHTML = `
      <button class="amb-row" data-a="off">${st.on ? "⬛ Выключить фон" : "▶ Включить фон"}</button>
      ${rows}
      <button class="amb-row" data-a="snd">${st.sound ? "🔊 звук вкл" : "🔇 звук выкл"}</button>
      <input id="whAmbAdd" placeholder="вставь ссылку YouTube…">`;
    panel.querySelectorAll(".amb-row").forEach(b => b.onclick = () => act(b));
    const inp = panel.querySelector("#whAmbAdd");
    inp.onkeydown = e => { if (e.key === "Enter") addCustom(inp.value); };
  }
  function row(src, name) {
    return `<button class="amb-row${st.on && st.src === src ? " act" : ""}" data-s="${src}">${name}</button>`;
  }

  function act(b) {
    if (b.dataset.s) { st.src = b.dataset.s; st.on = true; }
    else if (b.dataset.a === "off") st.on = !st.on;
    else if (b.dataset.a === "snd") st.sound = !st.sound;
    save(); mount(); render();
  }
  function addCustom(url) {
    const m = (url || "").match(/(?:v=|youtu\.be\/|embed\/)([\w-]{11})/);
    if (!m) return;
    st.custom = (st.custom || []).concat([{ id: m[1], name: "🎞 " + m[1] }]);
    st.src = "y:" + m[1]; st.on = true; save(); mount(); render();
  }

  function mount() {
    let layer = document.getElementById("whAmbLayer");
    if (!st.on) { if (layer) layer.remove(); restoreBg(); return; }
    // фон страницы прозрачный - слой (z-index:-1) виден «вместо белого»,
    // карточки остаются непрозрачными поверх
    document.documentElement.style.background = "transparent";
    document.body.style.background = "transparent";
    if (!layer) {
      layer = document.createElement("div"); layer.id = "whAmbLayer";
      document.body.prepend(layer);
    }
    if (!st.src && files.length) st.src = "f:" + files[0];
    let html = "";
    if (st.src.startsWith("f:")) {
      const u = "/assets/ambient/" + encodeURIComponent(st.src.slice(2));
      html = `<video data-k="${st.src}" src="${u}" autoplay loop playsinline ${st.sound ? "" : "muted"}></video>`;
    } else if (st.src.startsWith("y:")) {
      const id = st.src.slice(2);
      const p = `autoplay=1&mute=${st.sound ? 0 : 1}&loop=1&playlist=${id}&controls=0&rel=0&iv_load_policy=3`;
      html = `<iframe data-k="${st.src}:${st.sound}" src="https://www.youtube-nocookie.com/embed/${id}?${p}" allow="autoplay"></iframe>`;
    }
    const cur = layer.firstElementChild;
    const key = st.src.startsWith("f:") ? st.src : st.src + ":" + st.sound;
    if (!cur || cur.dataset.k !== key) layer.innerHTML = html;
    const v = layer.querySelector("video");
    if (v) { v.muted = !st.sound; v.play().catch(() => {}); }
  }
  function restoreBg() {
    document.documentElement.style.background = "";
    document.body.style.background = "";
  }
})();
