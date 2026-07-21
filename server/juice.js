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

  // ── ✨ Косметический XP: вылетающие числа, как в кликерах ──
  // ⚠ Это НЕ очки склада. Очки меряют энергию человека и потому редки; если
  // сыпать их на каждую коробку, экономика обесценится за неделю (см. шапку файла).
  // XP тут — чистый аттракцион: нигде не хранится, ни на что не влияет, живёт
  // 900мс и исчезает. Его работа — дать глазу «попадание» на КАЖДОЕ действие,
  // чего осознанно не делают очки.
  // Числа намеренно рваные (+17, +43), а не круглые: ровные суммы читаются как
  // тариф, рваные — как выигрыш.
  const XP_TIERS=[               // порог → цвет; крупная награда светится ярче
    [50,'#fbbf24','#78350f'],    // золото — редкий куш
    [35,'#c084fc','#3b0764'],    // фиолет
    [20,'#4ade80','#052e16'],    // зелень
    [0 ,'#60a5fa','#0c2d5e'],    // спокойный синий — мелочь
  ];
  // Углы карточки: числа летят ИЗ разных мест, чтобы глаз не привыкал к одной точке.
  // По горизонтали вынесены ЗА края карточки: изнутри они накрывали кнопки ответа
  // ровно в тот момент, когда по ним целятся мышью.
  const XP_CORNERS=[[-.04,.10,-1,-1],[1.04,.10,1,-1],[-.04,.86,-1,1],[1.04,.86,1,1]];
  let lastCorner=-1;
  // juiceXP(amount, anchorEl) — anchorEl задаёт рамку вылета (обычно карточка разбора)
  window.juiceXP=function(amount,anchorEl){
    amount=Math.max(1,Math.round(amount));
    const tier=XP_TIERS.find(t=>amount>=t[0]);
    let box=(anchorEl||document.body).getBoundingClientRect();
    // Карточки может не быть на экране (последняя коробка разобрана, блок скрыт) —
    // тогда rect нулевой и все числа сваливаются кучей в левый верхний угол.
    // Подменяем рамку на центр экрана: награда за ПОСЛЕДНЮЮ коробку самая ценная.
    if(box.width<40||box.height<40)
      box={left:innerWidth*.25,top:innerHeight*.22,width:innerWidth*.5,height:innerHeight*.4};
    const c=pick(XP_CORNERS,lastCorner);lastCorner=c;
    const[fx,fy,dx,dy]=XP_CORNERS[c];
    const jit=(n)=>(Math.random()-.5)*n;
    // clamp по экрану: на узком окне вынос за край карточки уехал бы за видимую область
    const x=Math.min(Math.max(box.left+box.width*fx+jit(40),52),innerWidth-52);
    const y=Math.min(Math.max(box.top+box.height*fy+jit(30),40),innerHeight-40);
    const el=document.createElement('div');
    el.textContent='+'+amount+' XP';
    el.style.cssText=`position:fixed;left:${x}px;top:${y}px;z-index:9999;pointer-events:none;`
      +`font-size:${amount>=50?30:amount>=35?25:amount>=20?21:18}px;font-weight:800;`
      +`color:${tier[1]};text-shadow:0 2px 10px ${tier[2]},0 0 22px ${tier[1]}55;`
      +'transform:translate(-50%,-50%) scale(.4);opacity:0;will-change:transform,opacity;';
    document.body.appendChild(el);
    // Три фазы: резкий «выброс» (глаз ловит появление) → ПАУЗА (число читается,
    // ради неё всё и затевалось: непрочитанная награда дофамина не даёт) → медленный
    // уплыв вверх-наружу. Гашение на ease-in: первую половину уплыва число ещё яркое.
    requestAnimationFrame(()=>{
      el.style.transition='transform .16s cubic-bezier(.2,1.6,.4,1),opacity .12s ease-out';
      el.style.transform='translate(-50%,-50%) scale(1)';el.style.opacity='1';
      setTimeout(()=>{
        el.style.transition='transform 1.5s cubic-bezier(.3,.5,.2,1),opacity 1.5s ease-in';
        el.style.transform=`translate(-50%,-50%) translate(${dx*46}px,${dy*20-64}px) scale(.92)`;
        el.style.opacity='0';
        setTimeout(()=>el.remove(),1550);
      },620);   // пауза висения - столько глаз тратит, чтобы считать двузначное число
    });
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
