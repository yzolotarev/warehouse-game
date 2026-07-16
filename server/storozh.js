/* 🏮 Сторож: чат о жизни и делах, всегда слева снизу на всех страницах
   (кроме окна захвата - оно на секунду). Мозг на сервере (POST /chat),
   история дня общая с Telegram-ботом. Read-only: коробки не двигает. */
(function () {
  "use strict";
  if (location.pathname === "/capture") return;
  const css = `
    #whChatBtn { position:fixed; left:14px; bottom:40px; z-index:60; cursor:pointer;
      font-size:18px; line-height:1; padding:9px 12px; border-radius:12px; user-select:none;
      background:var(--card,#222229); border:1px solid var(--line,#3a3a44);
      box-shadow:var(--card-shadow,0 4px 14px rgba(0,0,0,.35)); }
    #whChatBtn:hover { border-color:var(--accent,#ffb020); }
    #whChat { position:fixed; left:14px; bottom:88px; z-index:61;
      width:min(370px,calc(100vw - 28px)); height:min(480px,65vh);
      display:none; flex-direction:column; overflow:hidden;
      background:var(--bg,#16161a); color:var(--txt,#e8e6e1);
      border:1px solid var(--line,#3a3a44); border-radius:14px;
      box-shadow:0 14px 44px rgba(0,0,0,.45);
      font:14px/1.5 -apple-system,system-ui,sans-serif; }
    #whChat.open { display:flex; }
    #whChatHead { display:flex; align-items:center; gap:8px; padding:10px 13px;
      background:var(--card,#222229); border-bottom:1px solid var(--line,#3a3a44); }
    #whChatHead b { font-size:14px; }
    #whChatHead small { flex:1; color:var(--dim,#8a8894); }
    #whChatX { cursor:pointer; padding:2px 9px; border-radius:8px; color:var(--dim,#8a8894); }
    #whChatX:hover { color:var(--txt,#e8e6e1); background:var(--accent-soft,rgba(255,176,32,.12)); }
    #whChatLog { flex:1; overflow-y:auto; padding:12px;
      display:flex; flex-direction:column; gap:8px; }
    .wh-msg { max-width:86%; padding:8px 11px; border-radius:12px;
      white-space:pre-wrap; word-break:break-word; }
    .wh-msg.u { align-self:flex-end; border-bottom-right-radius:4px;
      background:var(--accent-soft,rgba(255,176,32,.16));
      border:1px solid var(--accent,#ffb020); }
    .wh-msg.a { align-self:flex-start; border-bottom-left-radius:4px;
      background:var(--card,#222229); border:1px solid var(--line,#3a3a44); }
    .wh-msg.wait { color:var(--dim,#8a8894); font-style:italic; }
    #whChatEmpty { margin:auto; text-align:center; color:var(--dim,#8a8894); padding:0 20px; }
    #whChatRow { display:flex; gap:8px; padding:10px;
      background:var(--card,#222229); border-top:1px solid var(--line,#3a3a44); }
    #whChatIn { flex:1; padding:9px 12px; border-radius:10px; outline:none;
      border:1px solid var(--line,#3a3a44); background:var(--bg,#16161a);
      color:inherit; font:inherit; }
    #whChatIn:focus { border-color:var(--accent,#ffb020); }
    #whChatSend { cursor:pointer; border:none; border-radius:10px; padding:0 15px;
      background:var(--accent,#d48806); color:#fff; font:inherit; }
    #whChatSend:disabled { opacity:.5; cursor:default; }`;
  addEventListener("DOMContentLoaded", () => {
    const st = document.createElement("style");
    st.textContent = css;
    document.head.appendChild(st);
    const btn = document.createElement("div");
    btn.id = "whChatBtn";
    btn.textContent = "🏮";
    btn.title = "Сторож: поговорить о жизни и делах";
    const win = document.createElement("div");
    win.id = "whChat";
    win.innerHTML = `
      <div id="whChatHead"><b>🏮 Сторож</b>
        <small>рефлексия · коробки не двигает</small>
        <span id="whChatX" title="закрыть (Esc)">✕</span></div>
      <div id="whChatLog"></div>
      <div id="whChatRow">
        <input id="whChatIn" placeholder="поговорить…" autocomplete="off">
        <button id="whChatSend">→</button></div>`;
    document.body.append(btn, win);
    const log = win.querySelector("#whChatLog");
    const inp = win.querySelector("#whChatIn");
    const send = win.querySelector("#whChatSend");
    let busy = false, loaded = false;
    const bubble = (cls, text) => {
      const d = document.createElement("div");
      d.className = "wh-msg " + cls;
      d.textContent = text;
      log.appendChild(d);
      log.scrollTop = log.scrollHeight;
      return d;
    };
    const showEmpty = () => {
      log.innerHTML = `<div id="whChatEmpty">Сижу, смотрю за складом.<br>
        Можно просто поболтать — о жизни, о делах.<br>
        <small>Тот же разговор, что с ботом в Telegram.</small></div>`;
    };
    const loadHist = async () => {
      try {
        const h = await (await fetch("/chat_history")).json();
        log.innerHTML = "";
        if (!h.msgs.length) return showEmpty();
        h.msgs.forEach(m => bubble(m.role === "user" ? "u" : "a", m.content));
      } catch (e) { showEmpty(); }
    };
    const open = () => {
      win.classList.add("open");
      if (!loaded) { loaded = true; loadHist(); }
      inp.focus();
    };
    const close = () => win.classList.remove("open");
    btn.onclick = () => win.classList.contains("open") ? close() : open();
    win.querySelector("#whChatX").onclick = close;
    addEventListener("keydown", e => {
      if (e.key === "Escape" && win.classList.contains("open")) close();
    });
    const ask = async () => {
      const q = inp.value.trim();
      if (!q || busy) return;
      busy = true;
      send.disabled = true;
      const empty = log.querySelector("#whChatEmpty");
      if (empty) empty.remove();
      bubble("u", q);
      inp.value = "";
      const wait = bubble("a wait", "…думает");
      try {
        const r = await fetch("/chat", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: q }) });
        const a = (await r.json()).answer;
        wait.textContent = a || "🌙 Сторож спит (мозг недоступен). Склад работает.";
        if (a) wait.classList.remove("wait");
      } catch (e) {
        wait.textContent = "🌙 Сторож спит (сервер молчит).";
      }
      log.scrollTop = log.scrollHeight;
      busy = false;
      send.disabled = false;
      inp.focus();
    };
    send.onclick = ask;
    inp.addEventListener("keydown", e => { if (e.key === "Enter") ask(); });
  });
})();

/* Heartbeat: даём серверу знать, что страница открыта (чтоб не слать notify-send, когда мы уже тут) */
(function () {
  "use strict";
  if (location.pathname === "/capture") return;
  const ping = () => { fetch("/heartbeat", { method: "POST", keepalive: true }).catch(() => {}); };
  ping();
  setInterval(ping, 60000);
})();
