/* "с возвращением": если склад простоял час+ без единого клика/клавиши
   где-либо в приложении (метка - whLastActive, см. theme.js/world.html) -
   при заходе в /hub мягкая фиолетовая виньетка по краям + трек.
   Это ОДНА сессия на всё приложение, не на страницу: музыка и рамка не
   обрываются при переходе между страницами - каждая следующая страница
   подхватывает воспроизведение с нужной секунды (см. whMusicStartedAt/
   whMusicUntil в localStorage). Бесшовно физически невозможно - между
   страницами честная перезагрузка документа, - но по ощущению продолжается.
   Живёт до конца трека, клики/навигация её не гасят. */
(function(){
  "use strict";
  const HOUR=60*60*1000;
  const TRACK_SRC="/assets/welcome-back.mp3";
  const TRACK_DURATION=253; // сек - длина обрезанного трека

  function injectDom(){
    let vign=document.getElementById("vignette");
    let audio=document.getElementById("welcomeAudio");
    if(vign&&audio)return{vign,audio};
    const style=document.createElement("style");
    style.textContent=
      "#vignette{position:fixed;inset:0;pointer-events:none;z-index:9999;opacity:0;transition:opacity 1.2s ease}"+
      "#vignette.on{opacity:1;animation:whVignettePulse 3.2s ease-in-out infinite}"+
      "#vignette.on.reactive{animation:none}"+
      "@keyframes whVignettePulse{0%,100%{box-shadow:inset 0 0 70px 8px rgba(168,85,247,.15)}50%{box-shadow:inset 0 0 95px 14px rgba(168,85,247,.26)}}";
    document.head.appendChild(style);
    vign=document.createElement("div");vign.id="vignette";
    audio=document.createElement("audio");audio.id="welcomeAudio";audio.src=TRACK_SRC;audio.preload="none";
    document.body.appendChild(vign);
    document.body.appendChild(audio);
    return{vign,audio};
  }

  function run(offsetSec){
    const{vign,audio}=injectDom();
    vign.classList.add("on");
    let vol=0,ramp=null,started=false,rafId=null;
    audio.volume=0;
    const fadeIn=()=>{
      clearInterval(ramp);
      ramp=setInterval(()=>{
        vol=Math.min(.6,vol+.03);
        audio.volume=vol;
        if(vol>=.6)clearInterval(ramp);
      },150);
    };
    const setOffset=()=>{try{audio.currentTime=offsetSec}catch(e){}};
    audio.addEventListener("loadedmetadata",setOffset,{once:true});
    setOffset();
    // анализ баса трека - рамка "дышит" синхронно с музыкой, а не по таймеру
    let analyser=null,data=null;
    try{
      const Ctx=window.AudioContext||window.webkitAudioContext;
      const actx=new Ctx();
      const src=actx.createMediaElementSource(audio);
      analyser=actx.createAnalyser();
      analyser.fftSize=256;
      analyser.smoothingTimeConstant=.65;
      src.connect(analyser);
      analyser.connect(actx.destination);
      data=new Uint8Array(analyser.frequencyBinCount);
      audio.addEventListener("play",()=>actx.resume().catch(()=>{}));
    }catch(e){}
    let minB=1,maxB=0,level=0;
    const tick=()=>{
      if(audio.paused||audio.ended){rafId=null;return}
      if(analyser){
        analyser.getByteFrequencyData(data);
        let sum=0;for(let i=1;i<8;i++)sum+=data[i]; // низ спектра - бас (0-й бин - DC, пропускаем)
        const bass=sum/7/255;
        minB=minB*.995+bass*.005; if(bass<minB)minB=bass;
        maxB=maxB*.995+bass*.005; if(bass>maxB)maxB=bass;
        const range=Math.max(maxB-minB,.05);
        let norm=Math.min(1,Math.max(0,(bass-minB)/range));
        norm=Math.pow(norm,1.4); // мягкий контур волны, без резких пиков
        level=norm>level?level*.85+norm*.15:level*.9+norm*.1; // почти симметрично плавно - дыхание, не рывок
        const glow=.06+level*.28,spread=2+level*22; // низкий потолок яркости - не бьёт по периферии
        vign.style.boxShadow="inset 0 0 "+(60+spread*1.6)+"px "+(5+spread)+"px rgba(168,85,247,"+glow.toFixed(2)+")";
      }
      rafId=requestAnimationFrame(tick);
    };
    const startReactive=()=>{
      vign.classList.add("reactive");
      if(!rafId)rafId=requestAnimationFrame(tick);
    };
    const endSession=()=>{
      vign.classList.remove("on","reactive");
      vign.style.boxShadow="";
      delete localStorage.whMusicUntil;
      delete localStorage.whMusicStartedAt;
    };
    audio.addEventListener("ended",endSession);
    // браузер блокирует автовоспроизведение со звуком без клика юзера, но
    // приглушённый (muted) autoplay обычно разрешён без него - стартуем так,
    // потом программно снимаем mute. Если и это заблокировано - первый клик/
    // клавиша сам станет тем самым разрешением браузера и запустит трек.
    audio.muted=true;
    addEventListener("load",()=>{
      audio.play().then(()=>{audio.muted=false;started=true;fadeIn();startReactive()}).catch(()=>{});
    });
    const unlock=()=>{
      if(started)return;
      document.removeEventListener("click",unlock);
      document.removeEventListener("keydown",unlock);
      audio.muted=false;
      audio.play().then(()=>{started=true;fadeIn();startReactive()}).catch(()=>{});
    };
    document.addEventListener("click",unlock);
    document.addEventListener("keydown",unlock);
  }

  function tryTrigger(){
    return false; // временно заморожено 21.07 по просьбе юзера - раскомментировать строку ниже, чтобы вернуть
    if(location.pathname!=="/hub")return false;
    const until=Number(localStorage.whMusicUntil||0);
    if(until>Date.now())return false; // сессия уже идёт где-то в приложении - не рестартовать
    const lastActive=Number(localStorage.whLastActive||0);
    if(!lastActive||Date.now()-lastActive<HOUR)return false;
    const now=Date.now();
    localStorage.whMusicStartedAt=now;
    localStorage.whMusicUntil=now+TRACK_DURATION*1000;
    run(0);
    return true;
  }

  function tryResume(){
    const until=Number(localStorage.whMusicUntil||0);
    if(!until||Date.now()>=until)return;
    const startedAt=Number(localStorage.whMusicStartedAt||0);
    const offset=(Date.now()-startedAt)/1000;
    if(offset<0||offset>=TRACK_DURATION)return;
    run(offset);
  }

  // скрипт подключён в <head> - document.body ещё не существует в момент
  // разбора файла, дожидаемся готовности документа перед вставкой элементов.
  function boot(){ tryTrigger(); } // tryResume() отключён вместе с tryTrigger - см. заморозку выше
  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",boot);
  else boot();
})();
