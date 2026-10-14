## Ролі колонок, схеми та зауваження щодо топології

role-top-l = основна колонка, ліва
role-top-r = основна колонка, права
role-top-mono = основна колонка (моно)
role-sub = сабвуфер
role-fill = фронтфіл
role-delay = затримані / балкон
role-zone = колонки в іншому приміщенні
role-monitor = монітор на сцені
role-unused = не колонки

variant-a-tops-and-sub-on-own-paths = Схема A: основні колонки й саб на власних матрицях або шинах.
variant-b-tops-on-main-sub-separate = Схема B: основні колонки просто з Main LR, саб окремо.
variant-c-tops-only = Схема C: основні колонки без окремого шляху саба.
variant-sub-shares-main = Саб висить на Main LR разом з основними колонками: кросовер налаштувати не можна.
variant-unknown = Основних колонок не знайдено.

topology-h-output = Вихід
topology-h-path = Джерело
topology-h-name = Назва
topology-h-role = Роль (визначена)
topology-h-confidence = Впевненість
topology-name = { $name }
topology-confidence = { $pct } %

issue-several-subs = Кілька шляхів схожі на саб ({ $paths }). Підтвердіть, який справжній.
issue-sub-pair = Саби стоять парою ({ $paths }); їх налаштовують як один.
issue-sub-on-mono-suggest-only = Саб на Main Mono. Запис туди ще не перевірено: програма покаже, що виставити вручну.
issue-sub-on-bus-suggest-only = Саб на шині { $bus }. Запис у шини ще не перевірено: програма покаже, що виставити вручну.
issue-playback-not-sent-to-sub-bus = Канали відтворення нічого не надсилають у шину саба { $bus }: під час заміру саб мовчатиме.
issue-sub-not-fed = Матриця саба { $matrix } ні від чого не отримує сигнал.
issue-sub-fed-by-main-and-mono = Матриця саба { $matrix } отримує і Main LR, і Mono: канали на Mono потраплять у саб двічі.
issue-main-hpf-feeds-sub = На Main LR стоїть зріз низів { $freq_hz } Гц, а саб живиться від Main: саб недоотримає низів.
issue-crossover-hpf-suggest-only = Основні колонки йдуть просто з Main LR. Не ставте ФВЧ кросовера на Main LR (саб теж живиться від нього): його ставлять у процесорі колонок або переводять колонки на матриці.
issue-main-and-matrix-tops = На виходах і Main L/R ({ $mains }), і матриці основних колонок ({ $matrices }). Імовірно, Main L/R ідуть на запис або у фоє. Підтвердіть.
issue-no-sub-path-ask = Окремого шляху саба немає. Якщо сабвуфер є, він, імовірно, висить на Main LR разом з основними колонками — тоді кросовер налаштувати не можна.
issue-other-speakers-audible = Шлях «{ $path }» увімкнено, він звучатиме під час заміру. Ці колонки в цій версії не налаштовуються.
issue-other-speakers-found = Знайдено шлях «{ $path }». Ці колонки в цій версії не налаштовуються.
issue-zone-will-hear-sweep = { $path } живить колонки в іншому приміщенні: там почують тестовий сигнал. Попередьте людей або вимкніть (mute) цей шлях.
issue-monitors-on-outputs = Монітори на виходах: { $paths }. Тестовий сигнал не має в них потрапити.
issue-side-conflict = Ліво й право не збігаються за назвою та за зв'язкою ({ $path }). Перевірте.
issue-existing-alignment = На шляхах колонок уже стоять затримка або полярність: { $items }.
issue-lcr-not-supported = Схема LCR із центральним кластером у цій версії не налаштовується.
issue-confirm-roles = { $count ->
    [one] { $count } вихід визначено непевно: підтвердіть його роль.
    [few] { $count } виходи визначено непевно: підтвердіть їхні ролі.
   *[many] { $count } виходів визначено непевно: підтвердіть їхні ролі.
}
