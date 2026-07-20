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
  // Боковой «удар» (#197): две вспышки по краям экрана. progress 0..1 красит их
  // от янтарного (начало разбора) к зелёному (пачка почти прогружена) — цвет сам
  // сообщает «сколько осталось», не отвлекая текстом.
  function impact(progress){
    let col;
    if(typeof progress==='number'&&isFinite(progress)){
      const p=Math.min(Math.max(progress,0),1);
      col=`hsla(${Math.round(38+92*p)},85%,55%,`; // 38°=янтарь → 130°=зелень
    }else{
      const cs=getComputedStyle(document.documentElement);
      const hex=cs.getPropertyValue('--ok').trim().replace('#','');
      col=`rgba(${parseInt(hex.slice(0,2),16)},${parseInt(hex.slice(2,4),16)},${parseInt(hex.slice(4,6),16)},`;
    }
    [['left','90deg'],['right','270deg']].forEach(([side,dir])=>{
      const el=document.createElement('div');
      el.style.cssText=`position:fixed;top:0;bottom:0;${side}:0;width:14vw;max-width:120px;`
        +`pointer-events:none;z-index:9998;background:linear-gradient(${dir},${col}.32) 0%,transparent 100%);`
        +`opacity:0;transform:scaleX(.55);transform-origin:${side};`
        +'transition:opacity .12s ease-out,transform .12s ease-out;';
      document.body.appendChild(el);
      requestAnimationFrame(()=>{el.style.opacity='1';el.style.transform='scaleX(1)'});
      setTimeout(()=>{el.style.transition='opacity .38s ease,transform .38s ease';
        el.style.opacity='0';el.style.transform='scaleX(.85)';
        setTimeout(()=>el.remove(),420)},170);
    });
  }
  // juice(kind?, progress?) — дёргать на любом рутинном микродействии.
  // progress (0..1) = прогрузка текущей пачки разбора, красит боковой удар.
  // Возвращает управление мгновенно.
  window.juice=function(kind,progress){
    if(Math.random()<0.75)tone(); // 25% тишины держат «попадания» свежими
    impact(progress);             // визуальный удар — всегда: он и есть прогресс-сигнал
  };

  // ── 🏆 Маленькая победа: тост снизу справа + короткая фанфара ──
  // juiceWin('Фокус протегирован!') — зовут конвейеры на закрытии этапа
  window.juiceWin=function(text){
    try{acx=acx||new(window.AudioContext||window.webkitAudioContext)();
      [523.25,659.25,783.99,1046.5].forEach((f,i)=>{   // до-ми-соль-до: фанфара
        const o=acx.createOscillator(),g=acx.createGain();
        o.type='sine';o.frequency.value=f;g.gain.value=.05;
        o.connect(g);g.connect(acx.destination);
        const t=acx.currentTime+i*.09;
        o.start(t);g.gain.setValueAtTime(.05,t);
        g.gain.exponentialRampToValueAtTime(1e-4,t+.22);o.stop(t+.23);
      });
    }catch(e){}
    const el=document.createElement('div');
    el.textContent='🏆 '+text;
    el.style.cssText='position:fixed;right:18px;bottom:18px;z-index:9999;'
      +'background:var(--card);border:1px solid var(--ok);color:var(--ok);'
      +'padding:10px 16px;border-radius:10px;font-size:14px;box-shadow:var(--card-shadow);'
      +'transform:translateY(60px);opacity:0;transition:all .3s ease;pointer-events:none';
    document.body.appendChild(el);
    requestAnimationFrame(()=>{el.style.transform='none';el.style.opacity='1'});
    setTimeout(()=>{el.style.transform='translateY(60px)';el.style.opacity='0';
      setTimeout(()=>el.remove(),350)},2600);
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
