// Слой juice: частая мелкая СЕНСОРНАЯ приятность на рутинных действиях склада.
// Намеренно ВНЕ очковой экономики: очки в 75% случаев = инфляция, экономика
// сдохла бы за неделю. Приятный микромомент — качает дофамин, ничего не инфлируя.
// ~75% срабатывает, 25% тишина: паузы держат «попадания» свежими.
// Звук + зелёная аура по краям (vignette) — вместо текста: не отвлекает от разбора.
(function(){
  const PENT=[523.25,587.33,659.25,783.99,880.00]; // до-ре-ми-соль-ля: подряд звучит музыкально, не назойливо
  let lastN=-1,acx=null;
  function pick(arr,last){let i;do{i=Math.floor(Math.random()*arr.length)}while(arr.length>1&&i===last);return i}
  function tone(){
    try{acx=acx||new(window.AudioContext||window.webkitAudioContext)();
      const n=pick(PENT,lastN);lastN=n;
      const f=PENT[n]*(Math.random()<.15?2:1); // изредка октавой выше — искорка
      const o=acx.createOscillator(),g=acx.createGain();
      o.type='sine';o.frequency.value=f;g.gain.value=.045;
      o.connect(g);g.connect(acx.destination);o.start();
      g.gain.exponentialRampToValueAtTime(1e-4,acx.currentTime+.18);o.stop(acx.currentTime+.19);
    }catch(e){}
  }
  function aura(){
    const el=document.createElement('div');
    const cs=getComputedStyle(document.documentElement);
    const ok=cs.getPropertyValue('--ok').trim();
    const hex=ok.replace('#','');
    const r=parseInt(hex.substring(0,2),16);
    const g=parseInt(hex.substring(2,4),16);
    const b=parseInt(hex.substring(4,6),16);
    el.style.cssText='position:fixed;inset:0;pointer-events:none;z-index:9998;'
      +`background:radial-gradient(ellipse at center,transparent 50%,rgba(${r},${g},${b},.13) 100%);`
      +'opacity:0;transition:opacity .3s ease;';
    document.body.appendChild(el);
    requestAnimationFrame(()=>{el.style.opacity='1';});
    setTimeout(()=>{el.style.opacity='0';setTimeout(()=>el.remove(),350)},450);
  }
  // juice(kind?) — дёргать на любом рутинном микродействии. kind пока не влияет.
  // Возвращает управление мгновенно.
  window.juice=function(kind){
    if(Math.random()>=0.75)return;   // 25% намеренной тишины
    tone();aura();
  };
})();
