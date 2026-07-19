/* 🌐 Интерфейс RU/EN. Хранится в localStorage.whLang ("ru" по умолчанию).
   Механика: русский остаётся языком исходников; при whLang=en словарь ниже
   на лету переводит текстовые узлы и атрибуты (title/placeholder/value/alt),
   MutationObserver подхватывает всё, что страницы дорисовывают динамически.
   Пользовательский контент (тексты задач, заметки, чат Сторожа) не трогается.
   Добавить перевод = добавить пару ["русская строка", "english string"]. */
(function () {
  "use strict";
  const LANG = localStorage.whLang === "en" ? "en" : "ru";

  /* пары [ru, en]; и точное совпадение, и подстрока внутри динамических строк.
     Применяются от длинных к коротким, порядок в списке не важен. */
  const PAIRS = [
    // ── общее ──
    ["← в прихожую склада", "← back to the warehouse lobby"],
    ["← в меню склада", "← back to the warehouse menu"],
    ["⛔ Склад стоит: разбери инбокс →", "⛔ Warehouse blocked: triage the inbox →"],
    ["⛔ Склад стоит: разбери входящие →", "⛔ Warehouse blocked: triage the inbox →"],
    ["⛔ Склад стоит: сначала разбери инбокс →", "⛔ Warehouse blocked: triage the inbox first →"],
    ["⛔ Склад стоит: сначала разбери инбокс (/terminal)", "⛔ Warehouse blocked: triage the inbox first (/terminal)"],
    ["⛔ Склад стоит: инбокс не был разобран. Разбери - и всё поедет.", "⛔ Warehouse blocked: the inbox wasn't triaged. Triage it and everything moves again."],
    ["тема: авто → светлая → тёмная", "theme: auto → light → dark"],
    ["тема: авто (по времени) → светлая → тёмная", "theme: auto (by clock) → light → dark"],
    ["день загружен: ", "day load: "],
    [" дн.", " days"],
    [" из ", " of "],
    ["▾ ещё", "▾ more"],
    ["▴ скрыть", "▴ hide"],
    ["← назад", "← back"],

    // ── хаб (меню) ──
    ["📋 Склад — меню", "📋 Warehouse — menu"],
    ["📋 Меню склада", "📋 Warehouse menu"],
    ["⭐ всего · сегодня +", "⭐ total · today +"],
    ["перекус-кредит: минуты ютуба, заработанные шагами", "snack credit: YouTube minutes earned with steps"],
    ["что сейчас", "what now"],
    ["▶ Разбирать", "▶ Triage"],
    ["▾ все полки и разборы", "▾ all shelves and triages"],
    ["разбор проектов", "project pass"],
    ["разбор отложки", "deferred pass"],
    ["Проекты", "Projects"],
    ["Отложка", "Deferred"],
    ["канон ≤5 · остальное вернуть, где было", "canon ≤5 · send the rest back where it was"],
    ["⛔ Склад стоит: входящие не разобраны — работа не двигается, пока не разберёшь", "⛔ Warehouse blocked: inbox not triaged — nothing moves until you do"],
    ["★ звезда дня:", "★ star of the day:"],
    ["Входящие — разбор", "Inbox — triage"],
    ["приёмка · всё, что ты записал", "receiving dock · everything you captured"],
    ["Фокус — делаю сейчас", "Focus — doing now"],
    ["не больше 5 · здесь же ⭐ на завтра", "5 max · tomorrow's ⭐ lives here too"],
    ["Проекты — дела в несколько шагов", "Projects — multi-step work"],
    ["паллетная зона · критерий готовности + шаги", "pallet zone · done-criteria + steps"],
    ["Ожидание — ждёшь других", "Waiting — on other people"],
    ["напомнит само, когда пора проверить", "pings you when it's time to check"],
    ["Отложенное — не на этой неделе", "Deferred — not this week"],
    ["стеллажи · по контекстам, всплывает на пересборке", "racks · by context, resurfaces at the general rebuild"],
    ["Мысли — ждут переноса в заметки", "Thoughts — waiting to become notes"],
    ["полка мыслей · перенеси в Obsidian или выкини", "thought shelf · move to Obsidian or toss"],
    ["Сделано сегодня", "Done today"],
    ["отгрузка · в", "shipping · at"],
    ["фура увезёт в архив", "the truck hauls it to the archive"],
    ["Статистика таймера", "Timer stats"],
    ["время на задачах · проекты · мета-анализ", "time on tasks · projects · meta-analysis"],
    ["Общая пересборка", "General rebuild"],
    ["итоги + ревизия · каждые 3 дня · +25 ⭐", "recap + audit · every 3 days · +25 ⭐"],
    ["🏭 мир — вайб-режим", "🏭 world — vibe mode"],
    ["вечерний зов в", "evening call at"],
    ["сделать меню стартовым", "make the menu the start page"],
    ["старт: меню ✓", "start: menu ✓"],
    ["⛔ Разблокировать склад: разобрать входящие", "⛔ Unblock the warehouse: triage the inbox"],
    ["физика мира: пока входящие не разобраны, всё стоит", "world physics: until the inbox is triaged, everything stands still"],
    ["📥 Разобрать входящие (", "📥 Triage the inbox ("],
    ["один вопрос на экран, вопросы сами разложат всё по полкам", "one question per screen — the questions shelve everything for you"],
    ["✨ Проверить ожидание (", "✨ Check waiting ("],
    ["коробки загорелись: пора чекнуть, дождался ли", "boxes lit up: time to check if it arrived"],
    ["столп №2: без неё система разваливается", "pillar #2: without it the system rots"],
    ["⭐ Отметить звезду на завтра", "⭐ Mark tomorrow's star"],
    ["две минуты: выбери главное — утром думать не придётся", "two minutes: pick the main thing — no thinking in the morning"],
    ["★ Делать звезду: ", "★ Work the star: "],
    ["решение принято ещё вчера — просто начни", "the decision was made yesterday — just start"],
    ["🎯 Работать фокус (", "🎯 Work focus ("],
    ["одна задача на экране — её и делай", "one task on screen — do that one"],
    ["☕ Всё разобрано — отдых тоже работа", "☕ All clear — rest is work too"],
    ["можно закинуть что-то во входящие или просто выдохнуть", "drop something in the inbox or just breathe"],
    ["пора!", "time!"],

    // ── терминал ──
    ["📦 Терминал склада", "📦 Warehouse terminal"],
    ["= разбор инбокса", "= inbox triage"],
    ["триаж входящих: каждая запись получает своё место - одно решение за раз", "inbox triage: every entry gets its place — one decision at a time"],
    ["🪞 Зеркало дня", "🪞 Mirror of the day"],
    ["Что записал сегодня (", "Captured today ("],
    [") — развернуть", ") — expand"],
    ["→ разбирать", "→ triage"],
    ["число дней, напр. 3", "days, e.g. 3"],
    ["✏️ переименовать", "✏️ rename"],
    ["жду человека/события — уйдёт в Ожидание, напомнит само", "waiting on a person/event — goes to Waiting, pings by itself"],
    ["жду человека/события - уйдёт в Ожидание, напомнит само", "waiting on a person/event — goes to Waiting, pings by itself"],
    ["⏳ Ожидание", "⏳ Waiting"],
    ["отправить в технический проект", "send to a tech project"],
    ["📡 Проект", "📡 Project"],
    ["✨ Инбокс пуст. Склад дышит ровно.", "✨ Inbox empty. The warehouse breathes easy."],
    ["⭐ Теперь отметь звезду на завтра → фокус", "⭐ Now mark tomorrow's star → focus"],
    ["↶ вернуть последнюю (Backspace)", "↶ undo last (Backspace)"],
    ["инбокс разобран", "inbox triaged"],
    ["🧹 Стеллаж пылится: ", "🧹 The rack is gathering dust: "],
    [" коробок, старейшей ", " boxes, oldest "],
    [" дн. → ревизия даст +", " days → an audit pays +"],
    [" очков за сегодня", " points today"],
    ["сегодня коробок не было", "no boxes today"],
    ["Полки: ", "Shelves: "],
    ["✨ загорелось в ожидании · осталось ", "✨ lit up in waiting · left: "],
    ["Дождался?", "Did it arrive?"],
    ["готово → Сделано · жду дальше → напомнит позже · в фокус → дождался, теперь делать · протухло → мусор", "done → Done · still waiting → pings later · to focus → arrived, now do it · stale → trash"],
    ["✅ Готово", "✅ Done"],
    ["⏳ Жду дальше", "⏳ Still waiting"],
    ["🎯 В фокус", "🎯 To focus"],
    ["🗑 Протухло", "🗑 Stale"],
    ["Это для технического проекта?", "Is this for a tech project?"],
    ["техпроект = отдельный репозиторий с кодом (сайт, бот, api) · если да — задача/идея уедет в инбокс проекта", "tech project = a separate code repository (site, bot, api) · if yes — the task/idea goes to that project's inbox"],
    ["🤖 Да, техпроект", "🤖 Yes, tech project"],
    ["🚫 Нет", "🚫 No"],
    ["Это дело?", "Is this actionable?"],
    ["дело = требует от тебя действия (проект — тоже дело) · мысль, ссылка, шум — не дело", "actionable = requires action from you (a project counts) · a thought, link, or noise doesn't"],
    ["✅ Дело", "✅ Actionable"],
    ["🚫 Не дело", "🚫 Not actionable"],
    ["Это один конкретный шаг?", "Is this one concrete step?"],
    ["один шаг = сел и сделал · проект = нескольких шагов или пока мутно → уедет в Проекты, там разложишь", "one step = sit down and do it · a project = several steps or still fuzzy → goes to Projects to lay out"],
    ["⚡ Один шаг", "⚡ One step"],
    ["🧱 Проект", "🧱 Project"],
    ["Оставить как мысль?", "Keep as a thought?"],
    ["мысль → сама станет заметкой в Obsidian · мусор исчезает навсегда", "a thought → becomes an Obsidian note by itself · trash disappears forever"],
    ["💭 Мысль", "💭 Thought"],
    ["🗑 Мусор", "🗑 Trash"],
    ["Займёшься на этой неделе?", "Will you do it this week?"],
    ["да → Фокус (делаю сейчас) · не сейчас → Отложенное: 🤖 сам разложит по контекстам", "yes → Focus (doing now) · not now → Deferred: 🤖 sorts into contexts by itself"],
    ["✅ Да", "✅ Yes"],
    ["🕰 Не сейчас", "🕰 Not now"],
    ["проход ", "pass "],
    [" · в пачке ещё ", " · left in batch: "],
    [" · ⚠ фокус уже полон (", " · ⚠ focus is already full ("],
    [") — честнее «не сейчас»", ") — “not now” is more honest"],
    [" · 🔁 уже ", " · 🔁 already "],
    ["× возвращал(а) в инбокс — реши сейчас, не откручивай снова", "× bounced back to inbox — decide now, stop re-looping"],
    [" · 🤖 похоже: ", " · 🤖 looks like: "],
    ["Не дело", "Not actionable"],
    ["Один шаг", "One step"],
    ["✔ уже сделано", "✔ already done"],
    ["сделано вне системы - в отгрузку, +3", "done outside the system — to shipping, +3"],
    ["не буду делать / не нужно - в мусор", "won't do / not needed — to trash"],
    ["Как часто напоминать?", "How often to remind?"],
    ["коробка уедет в Ожидание и сама загорится, когда пора проверить", "the box goes to Waiting and lights up by itself when it's time to check"],
    ["🌱 Реже и реже (1·3·7·14 дн.)", "🌱 Less and less often (1·3·7·14 days)"],
    ["⏰ Ближе к сроку (14·7·3·1 дн.)", "⏰ Closer to the deadline (14·7·3·1 days)"],
    ["🔁 Каждые N дней", "🔁 Every N days"],
    ["Каждые сколько дней?", "Every how many days?"],
    ["📡 Загружаю список проектов…", "📡 Loading the project list…"],
    ["📡 Нет техпроектов в реестре", "📡 No tech projects in the registry"],
    ["⚠ Ошибка загрузки списка", "⚠ Failed to load the list"],
    ["📡 Выбери проект:", "📡 Pick a project:"],
    ["задача/идея попадёт в инбокс проекта", "the task/idea lands in that project's inbox"],
    ["← Отмена", "← Cancel"],
    ["⚠ не отправилось (", "⚠ didn't send ("],
    ["⚠ не уехало (", "⚠ didn't move ("],
    [") — ответь ещё раз", ") — answer again"],
    ["⚠ не переименовалось (", "⚠ rename failed ("],
    ["⚠ не отметилось (", "⚠ didn't mark ("],
    ["Новое название коробки:", "New box name:"],
    ["🚶 уличная ✓ · убрать", "🚶 street ✓ · remove"],
    ["🚶 На улицу", "🚶 Street errand"],

    [" · ←→ листать", " · ←→ browse"],
    ["🤖 короче: ", "🤖 in short: "],

    // ── очередь «что сейчас» (/whatnow) ──
    ["🤖 Разметить для робота (", "🤖 Mark for the robot ("],
    ["две кнопки на коробку - и ИИ сможет брать твои задачи", "two buttons per box - and AI can pick up your tasks"],
    ["💭 Разобрать мысли (", "💭 Sort the thoughts ("],
    ["перенеси в Obsidian или выкини - полка не резиновая", "move them to Obsidian or toss them - the shelf isn't endless"],
    ["🥛 Подоить проекты (", "🥛 Milk the projects ("],
    ["сухие проекты: сними следующий шаг, чтобы фокус не пустел", "dry projects: pull the next step so focus doesn't run empty"],
    ["🧱 Оформить проекты (", "🧱 Form the projects ("],
    ["идеи ждут оформления: цель «готово, когда...» + первый шаг", "ideas await forming: a “done when…” goal + a first step"],
    ["дальше: ", "next: "],
    ["что сейчас — сделал одно, всплывёт следующее", "what now — finish one, the next pops up"],

    // ── разбор фокуса ──
    ["✂ Разбор фокуса", "✂ Focus trim"],
    ["проход 1: что может робот - уезжает к нему · проход 2: судьба остальных · Backspace = отменить", "pass 1: what the robot can do goes to it · pass 2: the rest's fate · Backspace = undo"],
    ["проход 1 · может ли это сделать ИИ? (осталось ", "pass 1 · can AI do this one? (left: "],
    ["проход 1 из 2 · потом судьба всех ", "pass 1 of 2 · then the fate of all "],
    ["✂ Проредить фокус (", "✂ Trim the focus ("],
    ["фокус толще канона: «делаю сейчас» превратился в стеллаж", "focus is fatter than canon: “doing now” has become a rack"],
    ["🎯 Остаётся в фокусе", "🎯 Stays in focus"],
    ["✅ Сделал", "✅ Did it"],
    ["🧱 обратно в проект", "🧱 back to its project"],
    ["🗄 на стеллаж", "🗄 to the rack"],
    ["⏳ в ожидание", "⏳ to waiting"],
    ["Разбор пройден. ✨", "Trim done. ✨"],
    ["В фокусе осталось: ", "Left in focus: "],
    [" - всё ещё толще канона, можно пройтись ещё раз", " - still fatter than canon, another pass wouldn't hurt"],
    ["осталось решить ", "left to decide: "],
    [" · в фокусе останется ~", " · staying in focus: ~"],

    // ── разбор по проектам ──
    ["📡 Разбор по проектам", "📡 Project triage"],
    ["Разбор по проектам", "Project triage"],
    ["задача про техпроект? → в его инбокс", "task about a tech project? → into its inbox"],
    ["задача про технический проект? отправь её в его инбокс · не проектная - едет дальше", "task about a tech project? send it to that project's inbox · not project-bound - moves on"],
    ["📡 Разобрать по проектам (", "📡 Sort into projects ("],
    ["проектные задачи - в инбоксы своих проектов, не в общую кучу", "project tasks belong in their projects' inboxes, not the common pile"],
    ["📋 Другой проект…", "📋 Other project…"],
    ["🚫 Не проектная", "🚫 Not project-bound"],
    ["Всё распределено. ✨", "Everything is routed. ✨"],
    ["За этот проход отправлено в проекты: ", "Sent to projects this pass: "],
    ["реестр проектов пуст", "the project registry is empty"],

    // ── роутинг в техпроекты ──
    ["📡 Ищу подходящий проект…", "📡 Looking for a matching project…"],
    ["📡 Похоже, это про:", "📡 Looks like it's about:"],
    ["📋 Другой (весь список)", "📋 Other (full list)"],

    // ── разбор для робота ──
    ["🤖 Разбор для робота", "🤖 Robot triage"],
    ["Разбор для робота", "Robot triage"],
    ["Робот / Не робот · что ИИ может сделать за тебя", "Robot / Not robot · what AI can do for you"],
    ["Робот = ИИ может сделать это целиком из компьютера · Не робот = нужен ты сам", "Robot = AI can do it entirely from the computer · Not robot = it needs you"],
    ["🤖 Робот", "🤖 Robot"],
    ["🙅 Не робот", "🙅 Not robot"],
    ["⤵ позже", "⤵ later"],
    ["пропустить, решу позже", "skip, decide later"],
    ["Всё размечено. ✨", "Everything is sorted. ✨"],
    ["🏆 Фокус протегирован!", "🏆 Focus fully tagged!"],
    ["За этот проход: ", "This pass: "],
    ["Новые коробки появятся здесь сами, как только родятся.", "New boxes will show up here on their own as they are born."],
    ["осталось ", "left: "],
    ["🎯 фокус", "🎯 focus"],
    ["🗄 стеллаж", "🗄 rack"],
    ["🧱 шаг проекта", "🧱 project step"],
    ["⏳ ожидание", "⏳ waiting"],

    // ── фокус ──
    ["🎯 Фокус", "🎯 Focus"],
    ["= делаю сейчас (≤5)", "= doing now (≤5)"],
    ["одна задача на экране - её и делай · ⭐ = главное на завтра, утром покажу её первой", "one task on screen — do that one · ⭐ = tomorrow's main thing, shown first in the morning"],
    ["🥛 Подоить проект: ", "🥛 Milk the project: "],
    ["сними с проекта следующий конкретный шаг — и он появится тут", "pull the next concrete step off the project — it shows up here"],
    ["Фокус пуст.", "Focus is empty."],
    ["🤖 отчёт ИИ: ", "🤖 AI report: "],
    ["Проекты ждут доения ↓", "Projects are waiting to be milked ↓"],
    ["Наполни его на разборе инбокса. ✨", "Fill it during inbox triage. ✨"],
    [" · 🕸 лежит ", " · 🕸 idle for "],
    ["(серия 🧱 +", "(series 🧱 +"],
    ["🎬 этот шаг даст +", "🎬 this step pays +"],
    [" мин перекуса (иногда 🎲 больше)", " snack minutes (sometimes 🎲 more)"],
    ["💡 в фокусе ещё шаг этой паллеты: второй за день пойдёт серией, с бонусом", "💡 another step of this pallet is in focus: the second one today counts as a series, with a bonus"],
    ["🗄 На стеллаж (позже)", "🗄 To the rack (later)"],
    ["снять звезду", "unstar"],
    ["⭐ на завтра", "⭐ for tomorrow"],
    ["☆ на завтра", "☆ for tomorrow"],
    ["звезда = главное на завтра (утром решений ноль)", "star = tomorrow's main thing (zero decisions in the morning)"],
    [" · родилась ", " · born "],
    ["✅ Сделал +", "✅ Did it +"],
    ["⏹ Стоп", "⏹ Stop"],
    ["▶ Таймер", "▶ Timer"],
    ["⏳ Жду", "⏳ Waiting"],
    ["уже неактуально", "no longer relevant"],
    ["🎲 Повезло! +", "🎲 Lucky! +"],
    [" мин перекуса вместо обычных", " snack minutes instead of the usual"],
    ["✅ Шаг закрыт. Проект «", "✅ Step closed. Project “"],
    ["» без следующего шага.", "” has no next step."],
    ["пока голова в контексте — задай следующий одной фразой (или пропусти):", "while your head is in context — set the next one in one phrase (or skip):"],
    ["следующий шаг — маленький и конкретный", "next step — small and concrete"],
    ["→ 🎯 Дальше", "→ 🎯 Next"],
    ["Пропустить →", "Skip →"],
    ["уже 2 ⭐ — сними одну, окрестность внимания не резиновая", "already 2 ⭐ — remove one, attention isn't elastic"],

    // ── проекты (паллеты) ──
    ["🧱 Паллетная зона", "🧱 Pallet zone"],
    ["🧱 Проекты", "🧱 Projects"],
    ["= дела в несколько шагов (паллетная зона)", "= multi-step work (pallet zone)"],
    ["здесь проект получает цель («готово, когда...») и следующий конкретный шаг. Шаг уходит в Фокус - список «делаю сейчас».", "here a project gets a goal (“done when…”) and the next concrete step. The step goes to Focus — the “doing now” list."],
    ["нет открытого проекта", "no open project"],
    [" шагов", " steps"],
    ["новых проектов на оформление: ", "new projects to shape: "],
    [" · шаг ", " · step "],
    ["Название проекта", "Project name"],
    ["Готово, когда...", "Done when…"],
    ["конкретный образ результата", "a concrete image of the result"],
    ["Первый шаг — маленький и конкретный", "First step — small and concrete"],
    ["настолько простой, что не о чем думать", "so simple there's nothing to think about"],
    ["Дальше →", "Next →"],
    ["Оформить", "Shape it"],
    ["‹ назад", "‹ back"],
    ["Позже ›", "Later ›"],
    ["передумал — проекта не будет", "changed my mind — no project"],
    ["🗑 Не нужен", "🗑 Not needed"],
    ["‹ к проектам (оформлю позже)", "‹ to projects (shape later)"],
    ["🌫 Новых проектов на оформление: ", "🌫 New projects to shape: "],
    ["Проектов нет. ✨ Проект рождается на разборе входящих: ответ «🧱 Проект» приводит его сюда.", "No projects. ✨ A project is born during inbox triage: answering “🧱 Project” brings it here."],
    ["пока пусто", "empty so far"],
    ["последнее: «", "last: “"],
    ["д назад", "d ago"],
    ["сегодня", "today"],
    ["🔖 встал на: ", "🔖 stuck at: "],
    ["+ 🔖 пометка «на чём встал»", "+ 🔖 note “where I stopped”"],
    ["🔖 На чём встал / почему (пусто — стереть):", "🔖 Where you stopped / why (empty — clear):"],
    ["🎯 в работе: ", "🎯 in progress: "],
    ["СЛЕДУЮЩИЙ ШАГ:", "NEXT STEP:"],
    ["взять в фокус — делать сейчас", "take to focus — do it now"],
    ["жду ответа/события — в Ожидание", "waiting on a reply/event — to Waiting"],
    ["🥛 очередь пуста — задай следующий шаг, иначе проект встанет", "🥛 the queue is empty — set the next step or the project stalls"],
    ["🕸 без движения ", "🕸 no movement for "],
    [" дн. (месяц — и уедет в морозилку)", " days (a month and it goes to the freezer)"],
    ["🔥 серия: сегодня по этой паллете уже сделано ", "🔥 series: already done on this pallet today: "],
    [" — следующий шаг даст +", " — the next step pays +"],
    [" сверху", " extra"],
    ["💡 второй шаг этой паллеты за день пойдёт серией: +", "💡 the second step of this pallet today counts as a series: +"],
    [" к очкам", " points extra"],
    ["🔥 серия: сегодня уже ", "🔥 series: today already "],
    ["💡 второй шаг за день пойдёт серией: +", "💡 the second step today counts as a series: +"],
    ["＋ в очередь", "＋ queue it"],
    ["в очередь проекта — заряженный следующий шаг (потом 🎯 в фокус)", "into the project queue — a loaded next step (then 🎯 to focus)"],
    ["ждунческий шаг: письмо, ответ, событие — сразу в Ожидание, напомнит само", "a waiting step: an email, a reply, an event — straight to Waiting, pings by itself"],
    ["Цель и шаги (", "Goal and steps ("],
    ["Зачем: ", "Why: "],
    ["Правила: ", "Rules: "],
    ["+ зачем и по каким правилам (необязательно)", "+ why and under what rules (optional)"],
    ["Готово, когда: ", "Done when: "],
    ["готово, когда: ", "done when: "],
    ["Заметка проекта", "Project note"],
    [" · нет", " · none"],
    ["💾 Сохранить в Obsidian", "💾 Save to Obsidian"],
    ["правь прямо здесь · перед записью делается бэкап", "edit right here · a backup is made before writing"],
    ["или быстро дописать строку в конец", "or quickly append a line at the end"],
    ["📎 привязать существующий файл", "📎 attach an existing file"],
    ["Управление", "Manage"],
    ["📌 На первое место", "📌 Bump to top"],
    ["клавиша P", "key P"],
    ["🧊 Заморозить проект", "🧊 Freeze the project"],
    ["творческие шаги не дают 🎬-минут: интерес — сам себе награда", "creative steps pay no 🎬 minutes: interest is its own reward"],
    ["🎨 творческий ✓ (без 🎬)", "🎨 creative ✓ (no 🎬)"],
    ["🎨 сделать творческим", "🎨 make it creative"],
    ["паллета ", "pallet "],
    [" · P = наверх", " · P = bump"],
    [" · морозилка", " · freezer"],
    ["заморожена", "frozen"],
    ["спит. Разморозка — только руками.", "asleep. Unfreezing is manual only."],
    ["▶️ Разморозить", "▶️ Unfreeze"],
    ["есть несохранённые правки", "unsaved edits"],
    ["⚠ файл параллельно изменён в Obsidian — скопируй правки, закрой и открой заметку заново", "⚠ the file changed in Obsidian in parallel — copy your edits, close and reopen the note"],
    ["💾 сохранено ", "💾 saved "],
    ["📁 в папке проекта: ", "📁 in the project folder: "],
    ["папка проектов Obsidian пуста или недоступна · допиши строку выше — файл создастся сам", "the Obsidian projects folder is empty or unavailable · append a line above — the file creates itself"],
    ["📎 Привязать", "📎 Attach"],
    ["💾 Сохранить", "💾 Save"],
    ["Зачем это вообще", "Why this at all"],
    ["чем мерить решения по ходу", "what to measure decisions against"],
    ["По каким правилам", "Under what rules"],
    ["я бы дал кому угодно полную свободу это делать, при условии что...", "I'd give anyone full freedom to do this, provided that…"],
    ["Удалить проект? Несделанные шаги уедут в мусор.", "Delete the project? Undone steps go to trash."],

    // ── стеллажи ──
    ["🗄 Стеллажи", "🗄 Racks"],
    ["🗄 Отложенное", "🗄 Deferred"],
    ["= сделаю позже, не на этой неделе (стеллажи)", "= later, not this week (racks)"],
    ["по одному товару: берёшь в работу, оставляешь лежать или выкидываешь", "one item at a time: take it on, leave it lying, or toss it"],
    ["LLM пересоберёт весь стеллаж в контексты", "the LLM re-sorts the whole rack into contexts"],
    ["· 🤖 пересобрать контексты", "· 🤖 rebuild contexts"],
    ["все товары одним списком, с поиском", "all items in one list, with search"],
    ["· 🔍 список", "· 🔍 list"],
    ["· 📦 по одному", "· 📦 one by one"],
    ["🤖 пересобираю контексты…", "🤖 rebuilding contexts…"],
    ["🤖 контексты пересоберутся после затишья", "🤖 contexts will rebuild after a lull"],
    ["🤖 контексты от ", "🤖 contexts from "],
    ["🤖 не вышло: ", "🤖 failed: "],
    ["🧹 Ревизия стеллажа: ", "🧹 Rack audit: "],
    [" дн. → +", " days → +"],
    ["🧹 Ревизия сегодня уже была - но можно ещё раз", "🧹 Today's audit is done — but you can go again"],
    ["ревизия готова", "audit done"],
    ["ревизия: ", "audit: "],
    ["ревизия · ", "audit · "],
    [") - честнее «оставить» или «пофиг»", ") — “keep” or “don't care” is more honest"],
    [" · лежит ", " · lying for "],
    [" · на стеллажах ещё ", " · still on the racks: "],
    ["🤖 контекст в пути", "🤖 context on its way"],
    ["Оставить ›", "Keep ›"],
    ["🗑 Пофиг", "🗑 Don't care"],
    ["🧹 Ревизия готова", "🧹 Audit done"],
    ["взял в фокус: ", "took to focus: "],
    [" · выкинул: ", " · tossed: "],
    [" · лежат дальше: ", " · still lying: "],
    ["← к стеллажу", "← back to the rack"],
    ["Стеллажи пусты. Всё либо в деле, либо сделано. ✨", "The racks are empty. Everything is either in motion or done. ✨"],
    ["Лежит дальше ›", "Keep lying ›"],
    ["поиск по стеллажам…", "search the racks…"],
    ["ничего не нашлось", "nothing found"],
    ["взять в руки", "pick it up"],
    ["Новое название:", "New name:"],

    // ── мысли ──
    ["💭 Мысли", "💭 Thoughts"],
    ["= уже в Obsidian", "= already in Obsidian"],
    ["каждая мысль с разбора автоматически стала заметкой в 2nd brain. Здесь — просто перечитай и убери с полки.", "every thought from triage automatically became a note in your 2nd brain. Here — just re-read and clear the shelf."],
    ["Полка мыслей пуста. ✨ Мысль попадает сюда с разбора входящих: «Не дело → 💭 Мысль».", "The thought shelf is empty. ✨ A thought lands here from inbox triage: “Not actionable → 💭 Thought”."],
    [" · записана ", " · captured "],
    ["✅ Ок → в архив", "✅ OK → archive"],
    ["🗑 Уже не нужна", "🗑 No longer needed"],
    ["заметка уже создана в Obsidian — кнопки убирают мысль только с этой полки", "the note already exists in Obsidian — these buttons only clear the thought off this shelf"],

    // ── отгрузка ──
    ["🚚 Отгрузка", "🚚 Shipping"],
    ["= отгрузка", "= shipping"],
    ["лежит на виду до", "on display until"],
    ["— потом фура увезёт в архив", "— then the truck hauls it to the archive"],
    ["Пока пусто. Сделай что-нибудь из 🎯 Фокуса — и оно ляжет сюда, на витрину дня.", "Empty so far. Do something from 🎯 Focus — it lands here, on the day's showcase."],
    ["↩ вернуть в фокус (не доделано)", "↩ back to focus (not finished)"],

    // ── общая пересборка ──
    ["столп №2: каждые 3 дня пройтись по всему складу, чтобы он не сгнил", "pillar #2: every 3 days walk the whole warehouse so it doesn't rot"],
    ["дней на складе", "days at the warehouse"],
    ["коробок прибыло", "boxes arrived"],
    ["отгружено", "shipped"],
    ["впереди: стеллажи (", "ahead: racks ("],
    [") → паллеты (", ") → pallets ("],
    [") → морозилка (", ") → freezer ("],
    ["награда за пересборку: +", "rebuild reward: +"],
    [" очков", " points"],
    ["очков", "points"],
    ["→ начать", "→ start"],
    ["🗄 стеллажи · осталось ", "🗄 racks · left: "],
    ["без контекста", "no context"],
    ["Ещё актуально?", "Still relevant?"],
    ["✅ Лежит дальше", "✅ Keep lying"],
    ["🗑 Уже нет", "🗑 Not anymore"],
    ["🧱 паллеты · осталось ", "🧱 pallets · left: "],
    ["шагов нет", "no steps"],
    ["Проект живёт? Дай следующий шаг — или заморозь.", "Is the project alive? Give it a next step — or freeze it."],
    ["следующий одношаговый шаг (уедет в 🎯)", "next single step (goes to 🎯)"],
    ["→ 🎯 шаг в фокус", "→ 🎯 step to focus"],
    ["Шаг уже есть ›", "Already has a step ›"],
    ["🧊 Заморозить", "🧊 Freeze"],
    ["🧊 морозилка · осталось ", "🧊 freezer · left: "],
    ["Размораживаем?", "Unfreeze it?"],
    ["🧊 Пусть спит", "🧊 Let it sleep"],
    ["Склад пересобран.", "The warehouse is rebuilt."],
    ["сегодня уже пересобран — очки не дублируются", "already rebuilt today — points don't double"],
    ["следующая пересборка через 3 дня — склад позовёт сам", "next rebuild in 3 days — the warehouse will call you"],
    ["⭐ и отметь звезду на завтра → фокус", "⭐ and mark tomorrow's star → focus"],
    ["→ в прихожую", "→ to the lobby"],

    // ── быстрый захват ──
    ["📦 Инбокс", "📦 Inbox"],
    ["что в голове?", "what's on your mind?"],
    ["напиши и нажми Enter…", "type and press Enter…"],
    ["Enter - отправить · Esc - закрыть", "Enter — send · Esc — close"],
    ["⚠ похоже на затрешенное: ", "⚠ looks like something you trashed: "],
    ["✅ принято", "✅ captured"],
    ["⚠ склад не отвечает - текст не сохранён", "⚠ warehouse not responding — text not saved"],

    // ── статистика таймера ──
    ["⏱ Таймер — статистика", "⏱ Timer — stats"],
    ["⏱ Статистика таймера", "⏱ Timer stats"],
    ["сколько времени на задачах · распределение по проектам · мета-анализ", "time spent on tasks · split by project · meta-analysis"],
    ["Неделя", "Week"],
    ["Месяц", "Month"],
    ["День", "Day"],
    ["Всего", "Total"],
    ["Задач", "Tasks"],
    ["Среднее", "Average"],
    ["По проектам", "By project"],
    ["По контекстам", "By context"],
    ["Нет данных", "No data"],
    ["За компом vs на задачах", "At the computer vs on tasks"],
    ["Из них на задачах", "Of that, on tasks"],
    ["За компом", "At the computer"],
    ["недоступен", "unavailable"],

    // ── сторож ──
    ["Сторож: поговорить о жизни и делах", "Watchman: talk about life and work"],
    ["🏮 Сторож", "🏮 Watchman"],
    ["рефлексия · коробки не двигает", "reflection · never moves boxes"],
    ["закрыть (Esc)", "close (Esc)"],
    ["поговорить…", "talk…"],
    ["Сижу, смотрю за складом.", "Sitting here, watching the warehouse."],
    ["Можно просто поболтать — о жизни, о делах.", "We can just chat — about life, about work."],
    ["Тот же разговор, что с ботом в Telegram.", "Same conversation as with the Telegram bot."],
    ["…думает", "…thinking"],
    ["🌙 Сторож спит (мозг недоступен). Склад работает.", "🌙 The watchman is asleep (brain unavailable). The warehouse still works."],
    ["🌙 Сторож спит (сервер молчит).", "🌙 The watchman is asleep (server silent)."],

    // ── мир (изометрия) ──
    ["🏭 Склад", "🏭 Warehouse"],
    ["звезда дня — выбрана с вечера", "star of the day — picked in the evening"],
    ["обычный режим: списки и кнопки", "regular mode: lists and buttons"],
    ["☰ меню", "☰ menu"],
    ["повернуть камеру (или клавиша R)", "rotate the camera (or key R)"],
    ["☕ Перерыв — главное встать из-за стола", "☕ Break — the point is to stand up"],
    ["медитация, прогулка, кофе — что угодно не за столом · +2 ⭐", "meditation, a walk, coffee — anything away from the desk · +2 ⭐"],
    ["Склад отдыхает — и ты отдыхай", "The warehouse is resting — you rest too"],
    ["вернуться раньше (без очков)", "come back early (no points)"],
    ["⛔ Склад стоит: приёмка не разобрана — жми сюда", "⛔ Warehouse blocked: receiving not triaged — click here"],
    ["🧰 Пора пересобрать склад (Week Review) · +25 →", "🧰 Time to rebuild the warehouse (Week Review) · +25 →"],
    [" · сегодня +", " · today +"],
    [" · фура ", " · truck "],
    ["зов ", "call "],
    [" · ЛКМ тащить · колесо зум · R повернуть · I разбор", " · LMB drag · wheel zoom · R rotate · I triage"],
    ["ПРИЁМКА", "RECEIVING"],
    ["инбокс → разбор", "inbox → triage"],
    ["ТЕРМИНАЛ", "TERMINAL"],
    ["разбор по одной", "one-by-one triage"],
    ["ФОКУС", "FOCUS"],
    ["делаю сейчас ≤5", "doing now ≤5"],
    ["КОФЕЙНЯ", "COFFEE BAR"],
    ["отдых — тоже работа", "rest is work too"],
    ["СТЕЛЛАЖИ", "RACKS"],
    ["отложено · позже", "deferred · later"],
    ["ПАЛЛЕТЫ", "PALLETS"],
    ["проекты", "projects"],
    ["ОЖИДАНИЕ", "WAITING"],
    ["ждёшь других", "waiting on others"],
    ["ОТГРУЗКА", "SHIPPING"],
    ["сделано сегодня", "done today"],
    [" · без панели", " · no panel"],
  ];

  /* динамические числовые форматы, которые не берутся подстроками */
  const RX = [
    [/(\d+)\s*ч\s+(\d+)\s*м/g, "$1h $2m"],
    [/(^|[\s(>])(\d+)м(?=$|[\s·▶,)<])/g, "$1$2m"],
    [/^(\d+) мин$/, "$1 min"],
    [/этап (\d+)\/(\d+) пуст/g, "stage $1/$2 empty"],
    [/этап (\d+)\/(\d+)/g, "stage $1/$2"],
  ];

  const CYR = /[А-Яа-яЁё]/;
  const sorted = PAIRS.slice().sort((a, b) => b[0].length - a[0].length);
  const exact = new Map(PAIRS.map(p => [p[0], p[1]]));

  function translate(s) {
    if (LANG !== "en" || !s || !CYR.test(s)) return s;
    const trimmed = s.trim();
    const hit = exact.get(trimmed);
    if (hit !== undefined) return s.replace(trimmed, hit);
    let out = s;
    for (const [ru, en] of sorted) if (out.includes(ru)) out = out.split(ru).join(en);
    for (const [re, en] of RX) out = out.replace(re, en);
    return out;
  }
  window.WH_T = translate; // для canvas-текста в мире и prompt/confirm

  if (LANG === "en") {
    // пользовательский контент не переводим (тексты задач, заметки, чат, имена)
    const SKIP = ".task,.raw,.ptitle,#boxtext,#whChatLog,#notebody,#lenta,.bar-lbl,#starT,.step";
    const txNode = n => {
      const p = n.parentElement;
      if (p && (p.closest(SKIP) || p.tagName === "SCRIPT" || p.tagName === "STYLE")) return;
      const t = translate(n.nodeValue);
      if (t !== n.nodeValue) n.nodeValue = t;
    };
    const txAttrs = el => {
      if (!el.getAttribute) return;
      for (const a of ["title", "placeholder", "value", "alt"]) {
        const v = el.getAttribute(a);
        if (v && CYR.test(v)) {
          const t = translate(v);
          if (t !== v) el.setAttribute(a, t);
        }
      }
    };
    const walk = node => {
      if (node.nodeType === 3) { txNode(node); return; }
      if (node.nodeType !== 1 || node.tagName === "SCRIPT" || node.tagName === "STYLE") return;
      txAttrs(node);
      for (const c of node.childNodes) walk(c);
    };
    new MutationObserver(muts => {
      for (const m of muts) {
        if (m.type === "characterData") txNode(m.target);
        else if (m.type === "attributes") txAttrs(m.target);
        else m.addedNodes.forEach(walk);
      }
    }).observe(document.documentElement, {
      subtree: true, childList: true, characterData: true,
      attributes: true, attributeFilter: ["title", "placeholder", "value", "alt"],
    });
    addEventListener("DOMContentLoaded", () => {
      document.documentElement.lang = "en";
      document.title = translate(document.title);
      walk(document.body);
    });
  }

  /* кнопка-переключатель (рядом с переключателем темы) */
  addEventListener("DOMContentLoaded", () => {
    if (location.pathname === "/capture") return; // окно на секунду - не захламляем
    const b = document.createElement("div");
    b.id = "langTgl";
    b.textContent = LANG === "en" ? "RU" : "EN";
    b.title = LANG === "en" ? "Переключить интерфейс на русский" : "Switch the interface to English";
    b.style.cssText = "position:fixed;right:64px;bottom:12px;z-index:50;font-size:13px;font-weight:600;" +
      "letter-spacing:.04em;color:var(--txt,#e8e6e1);cursor:pointer;user-select:none;padding:10px 12px;" +
      "border-radius:10px;background:var(--card,#222229);border:1px solid var(--line,#3a3a44);" +
      "box-shadow:var(--card-shadow,0 4px 14px rgba(0,0,0,.35))";
    b.onclick = () => { localStorage.whLang = LANG === "en" ? "ru" : "en"; location.reload(); };
    document.body.appendChild(b);
  });
})();
