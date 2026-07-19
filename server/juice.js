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

  // ── 💥 Перебор! — вспышка на кнопке "в фокус" если >5 ──
  window.focusWarn=async function(btnEl){
    let count;
    try{count=((await (await fetch('/state')).json()).counts||{}).focus||0}catch(e){count=0}
    if(count<=5)return;
    if(!btnEl)btnEl=document.querySelector('#stage button, .b-primary, [onclick*=focus]');
    if(!btnEl)return;
    const msg=document.createElement('div');
    msg.textContent=`⚠ Перебор! ${count} задач`;
    msg.style.cssText='position:absolute;bottom:calc(100%+6px);left:50%;transform:translateX(-50%);'
      +'background:var(--danger,#b3261e);color:#fff;font-size:12px;font-weight:600;'
      +'padding:4px 12px;border-radius:6px;white-space:nowrap;z-index:10;'
      +'animation:fadeUpOut 3s ease forwards;';
    const p=btnEl.parentElement;if(p){p.style.position='relative';p.appendChild(msg);}
    btnEl.style.animation='shake .4s ease';
    btnEl.style.background='var(--danger,#b3261e)';btnEl.style.color='#fff';
    setTimeout(()=>{btnEl.style.animation='';btnEl.style.background='';btnEl.style.color='';},3000);
    tone();tone();
    setTimeout(()=>msg.remove(),3100);
  };
})();

// ── Shake + fadeUpOut keyframes (global) ──
const __fw_style=document.createElement('style');
__fw_style.textContent=`
@keyframes shake{0%,to{transform:translateX(0)}20%{transform:translateX(-6px)}40%{transform:translateX(6px)}60%{transform:translateX(-4px)}80%{transform:translateX(4px)}}
@keyframes fadeUpOut{0%{opacity:1;transform:translateX(-50%) translateY(0)}70%{opacity:1;transform:translateX(-50%) translateY(-4px)}to{opacity:0;transform:translateX(-50%) translateY(-10px)}}
`;
document.head.appendChild(__fw_style);
