## Кроки майстра. Перший абзац кожного екрана каже, що зараз станеться.

step-welcome-title = Початок
step-welcome-what = Ми виміряємо залу й налаштуємо звукову систему на пульті. У порожній залі це забере близько години.
step-welcome-venue = Яка це зала?
step-welcome-venue-known = { $venue } (уже вимірювали)
step-welcome-venue-new = Нова зала
step-welcome-venue-name = Назва зали (наприклад, «Велика зала»):
step-welcome-goal = Що робимо сьогодні?
step-welcome-goal-full = Повне налаштування: замір, пропозиції й перевірка
step-welcome-goal-verify = Лише перевірити, як звучить зараз
step-welcome-goal-sub = Лише сабвуфер
step-welcome-goal-report = Лише звіт за минулу сесію

step-mac-title = Перевірка Mac
step-mac-what = Спершу перевіримо цей Mac: звукове під'єднання до пульта й потрібні налаштування.
step-mac-dev-safe = Режим розробки: використовуються лише емулятор пульта й модель зали.
step-mac-no-audio = Не знайдено звукового пристрою зі входами й виходами.
step-mac-audio-problems = Звуковий пристрій { $name }: { $problems } (потрібно 32 канали на 48 кГц).
step-mac-audio-ok = Звуковий пристрій { $name } готовий.
step-mac-fix-audio = Під'єднайте USB-кабель від пульта й запустіть знову. В «Налаштуванні Audio-MIDI» карта має працювати на 48 кГц.

step-connect-title = Пульт
step-connect-searching = Шукаю пульт у мережі…
step-connect-none = Пульт не відповів.
step-connect-enter-ip = Введіть адресу пульта (на пульті: Setup → Network), наприклад 192.168.x.x:
step-connect-found = Знайдено { $model } «{ $name }» за адресою { $host } (прошивка { $firmware }). Це ваш пульт?
step-connect-option = { $model } «{ $name }», { $host }
step-connect-several = Відповіли кілька пультів. Який ваш?
step-connect-fix = Перевірте, що Mac і пульт в одній мережі, і вимкніть VPN. У macOS дозвольте цей термінал: Системні параметри → Приватність і безпека → Локальна мережа.
step-connect-link = Зв'язок перевірено: втрачено { $loss_pct } %, відповідь за { $median_ms } мс.
step-connect-firmware-unverified = Прошивку { $firmware } із цією програмою не перевіряли. Програма лише читатиме й підказуватиме.

step-read-title = Читання пульта
step-read-progress = Читаю пульт
step-read-done = Прочитано налаштувань: { $count }. Нічого не змінено.
step-read-unparsed = { $count ->
    [one] { $count } налаштування не вдалося розібрати; його збережено у файлі.
    [few] { $count } налаштування не вдалося розібрати; їх збережено у файлі.
   *[many] { $count } налаштувань не вдалося розібрати; їх збережено у файлі.
}
step-read-save-scene = Збережіть зараз сцену на пульті: SCENES → вільний слот → SAVE, назва «before-ctune-{ $date }». Так усе можна повернути й без комп'ютера.

step-topology-title = Колонки
step-topology-what = Ось колонки, знайдені на виходах пульта. Перевірте ролі.
step-topology-correct = Усе правильно?
step-topology-ask-role = { $output } (джерело { $path }, назва «{ $name }»): що сюди під'єднано?

step-equipment-title = Обладнання
step-equipment-what = Кілька запитань, на які пульт не відповідає.
step-equipment-speakers = Колонки активні чи пасивні з підсилювачем?
step-equipment-active = Активні (з вбудованим підсилювачем)
step-equipment-passive = Пасивні з підсилювачем
step-equipment-hall = Розмір зони слухачів у метрах, ширина × глибина (наприклад 14 × 22). Можна залишити порожнім — візьмемо стандартний план.
step-equipment-mic-count = Скільки у вас вимірювальних мікрофонів?
step-equipment-mics = { $count ->
    [one] { $count } мікрофон
    [few] { $count } мікрофони
   *[many] { $count } мікрофонів
}
step-equipment-mic-model = Яка модель?
step-equipment-model = { $model }

step-mics-title = Мікрофони
step-mics-what = Зараз знайдемо, на якому вході пульта кожен вимірювальний мікрофон.
step-mics-plug = Під'єднайте мікрофони до стейджбоксу або до пульта й увімкніть на цих входах +48V. Сама програма фантомне живлення не вмикає.
step-mics-scratch = Натисніть Enter, підійдіть до мікрофона { $number } і злегка пошкрябайте нігтем по його сітці. Слухаємо 15 секунд.
step-mics-found = Мікрофон { $number } — на USB-вході { $usb }.
step-mics-nothing = Нічого не почув. Перевірте +48V, кабель і вхід, потім ще раз.
step-mics-ambiguous = Відреагували одразу кілька входів. Торкайтеся лише одного мікрофона.
step-mics-already-mapped = Це USB-вхід { $usb }, його вже знайдено. Пошкрябайте наступний мікрофон.
step-mics-stop-here = Продовжити зі знайденими мікрофонами
step-mics-not-preamp = { $mic } іде не з мікрофонного входу. Перевірте маршрутизацію карти.
step-mics-phantom-off = { $mic }: на передпідсилювачі (headamp { $headamp }) вимкнено +48V. Увімкніть.
step-mics-console-checks = Зробіть це на пульті й натисніть Enter:

step-path-title = Шлях відтворення
step-path-no-playback = Жоден канал не грає звук комп'ютера. Призначте USB-виходи карти 1 і 2 на два канали й надішліть їх у Main LR.
step-path-assign = Канал { $channel } грає звук комп'ютера: увімкніть його й призначте на Main LR.
step-path-console-checks = Зробіть це на пульті й натисніть Enter:
step-path-test = Тиха перевірка: короткий тон і трохи шуму на −40 dBFS, близько 3 секунд.
step-path-latency = { $ms } мс
step-path-h-latency = Затримка
step-path-heard = Усі мікрофони чують колонки.
step-path-not-heard = Деякі мікрофони нічого не чують. Перевірте, що канали відтворення й Main LR підняті й не вимкнені (mute).

step-level-title = Рівень
step-level-what = Зараз підберемо комфортну гучність заміру — за мікрофонами.
step-level-quiet = Будь ласка, 3 секунди тиші в залі: слухаємо фоновий шум.
step-level-noise-hints = Фоновий шум може завадити заміру:
step-level-ramp = Рожевий шум (рівне шипіння) почнеться дуже тихо й повільно наростатиме, доки мікрофон не почує достатньо. Потім кілька коротких тестових свипів (тон, що плавно йде від низьких до високих) підберуть рівень заміру.
step-level-ramp-target = Рівень знайдено: { $level } dBFS на першому мікрофоні.
step-level-ramp-above-target = Рівень знайдено, трохи зависокий: { $level } dBFS на першому мікрофоні.
step-level-ramp-output-limit = Вихід комп'ютера дійшов до безпечного максимуму раніше, ніж мікрофон почув достатньо ({ $level } dBFS).
step-level-ramp-clip = Мікрофон перевантажився: зменште його gain ({ $level } dBFS).
step-level-ramp-jump = Рівень різко підскочив ({ $level } dBFS): хтось рухав фейдер?
step-level-ramp-no-response = Звук став гучнішим, а мікрофон цього не почув ({ $level } dBFS). Не той вхід, від'єднано чи звук не йде з колонок?
step-level-ramp-user = Зупинено.
step-level-ramp-ended = Звук припинився раніше, ніж знайшовся рівень.
step-level-not-target = Рівень не ідеальний. Усуньте причину й повторіть або продовжуйте як є.
step-level-continue = Продовжити з цим рівнем
step-level-sweep = Рівень заміру: { $level } dBFS.
step-level-sweep-output-limit = Навіть на безпечному максимумі мікрофон чує мало. Додайте gain мікрофона на пульті.
step-level-sweep-too-loud-at-min = Надто гучно навіть на мінімальному рівні: зменште gain мікрофона або фейдери Main.
step-level-sweep-no-response = Мікрофон не реагує на тестовий сигнал. Перевірте вхід і кабель.

step-calibration-title = Калібрування мікрофонів
step-calibration-reuse = Ці мікрофони нещодавно калібрували; використовуємо те калібрування.
step-calibration-what = Порівняємо мікрофони між собою, щоб їхні невеликі відмінності не вважати відмінностями в залі.
step-calibration-place = Поставте всі мікрофони поруч: капсулі в межах 10 см, на одній висоті, дивляться в один бік, за 3–5 м від колонок.
step-calibration-sweeps = Два тестові свипи (тон, що плавно йде від низьких до високих), близько 20 секунд.
step-calibration-bad-sweeps = Калібрувальні свипи вийшли нечистими (перевантаження, шум або рух). Перевірте gain і тишу та повторіть.
step-calibration-sensitivity = { $db } дБ
step-calibration-h-sens = Чутливість
step-calibration-done = Калібрування готове.
step-calibration-polarity-ambiguous = У половини мікрофонів зворотна полярність: перевірте кабелі всіх мікрофонів.
step-calibration-pair-differs = Один із двох мікрофонів відрізняється, але який — не зрозуміти. Перевірте обидва.
step-calibration-exclude = { $count ->
    [one] { $mics } відрізняється від інших. Виключити його із заміру?
   *[other] { $mics } відрізняються від інших. Виключити їх із заміру?
}

step-points-title = Замір
step-points-what = Зараз виміряємо залу в кількох місцях, де сидять люди.
step-points-plan = Місць для заміру: { $count }.
step-points-place = { $mic } → місце { $point } з { $total } ({ $place })
step-points-height = Капсуль на висоті вух людини, що сидить (1,2 м), спрямований на сцену.
step-points-bad = { $count ->
    [one] { $count } місце виміряно нечисто:
    [few] { $count } місця виміряно нечисто:
   *[many] { $count } місць виміряно нечисто:
}
step-points-keep = Залишити як є
step-points-done = Виміряно місць: { $count }.

step-results-title = Підсумки
step-results-no-points = Поки немає виміряних місць.
step-results-what = Що показав замір у { $count } місцях:
step-results-next-milestones = Пропозиції змін (кросовер, вирівнювання саба, EQ системи) і їх застосування з'являться в наступній версії. Ваш замір збережено для них.
finding-hump = Горб біля { $freq_hz } Гц (+{ $gain_db } дБ): голос може бубоніти.
finding-rt60-dry = Зала радше суха (RT60 { $rt60_s } с): добре для мовлення.
finding-rt60-good = Зала помірно гулка (RT60 { $rt60_s } с): добре і для мовлення, і для музики.
finding-rt60-boomy = Зала гулка (RT60 { $rt60_s } с): мовлення менш розбірливе. EQ цього не виправить — допоможуть штори й поглиначі.
finding-rt60-treatment = Зала дуже гулка (RT60 { $rt60_s } с): для розбірливого мовлення потрібна акустична обробка. EQ цього не виправить.
finding-spread-small = Звук у різних місцях схожий (розкид { $spread_db } дБ).
finding-spread-large = Звук у різних місцях дуже відрізняється (розкид { $spread_db } дБ): тут напрям колонок важливіший за EQ.

step-finish-title = Що далі
step-finish-what = На сьогодні все. Перед тим як піти:
step-finish-listen = На найближчому богослужінні послухайте мовлення й музику з кількох місць.
step-finish-mics = { $count ->
    [one] Від'єднайте вимірювальний мікрофон, потім увімкніть його канал, якщо вимикали.
    [few] Від'єднайте { $count } вимірювальні мікрофони, потім увімкніть їхні канали, якщо вимикали.
   *[many] Від'єднайте { $count } вимірювальних мікрофонів, потім увімкніть їхні канали, якщо вимикали.
}
step-finish-saved = Усе збережено в { $path }.
step-connect-not-this-one = Тоді введіть адресу вашого пульта.
step-equipment-hall-invalid = Такий розмір не підходить: потрібні два числа від 3 до 200 метрів, спершу ширина.
step-equipment-model-other = Інша модель
step-mics-listening = Слухаємо мікрофон { $number }: залишилося { $seconds } с
step-mics-mute-channels = { $count ->
    [one] Вимкніть (mute) канал { $channels }: він слухає вимірювальний мікрофон, а мікрофони не мають потрапити в колонки (буде зав'язка).
   *[other] Вимкніть (mute) канали { $channels }: вони слухають вимірювальні мікрофони, а мікрофони не мають потрапити в колонки (буде зав'язка).
}
step-mics-rereading = Перевіряю пульт перед першим звуком
step-mics-still-open = { $count ->
    [one] Канал { $channels } досі надсилає вимірювальний мікрофон у колонки. Вимкніть його (mute) на пульті й натисніть Enter.
   *[other] Канали { $channels } досі надсилають вимірювальний мікрофон у колонки. Вимкніть їх (mute) на пульті й натисніть Enter.
}
step-mics-why-mute = Інакше тестовий сигнал через мікрофон дасть зав'язку.
step-path-what = Зараз перевіримо, що звук комп'ютера йде в колонки зали, а не в монітори.
step-path-close-sends = { $count ->
    [one] Канал { $channels }: приберіть посили в шини { $buses }, щоб тестовий сигнал не потрапив у монітори на сцені й у трансляцію.
   *[other] Канали { $channels }: приберіть посили в шини { $buses }, щоб тестовий сигнал не потрапив у монітори на сцені й у трансляцію.
}
step-points-sweeps = Два тестові свипи, близько 15 секунд. Після Enter у вас 3 секунди, щоб відійти щонайменше на 2 м.
point-front-left = передні ряди, ліворуч
point-front-centre = передні ряди, по центру
point-front-right = передні ряди, праворуч
point-middle-left = середина, ліворуч
point-middle-right = середина, праворуч
point-back-left = задні ряди, ліворуч
point-back-centre = задні ряди, по центру
point-back-right = задні ряди, праворуч
point-number = місце { $number }
step-finish-undo-by-hand = Поверніть те, що сьогодні змінювали на пульті вручну (mute, посили).
step-points-put = { $count ->
    [one] Поставте мікрофон сюди:
   *[other] Поставте мікрофони в ці місця:
}
