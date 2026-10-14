## Шаги мастера. Первый абзац каждого экрана говорит, что сейчас произойдёт.

step-welcome-title = Начало
step-welcome-what = Мы измерим зал и настроим звуковую систему на пульте. В пустом зале это займёт около часа.
step-welcome-venue = Какой это зал?
step-welcome-venue-known = { $venue } (уже измеряли)
step-welcome-venue-new = Новый зал
step-welcome-venue-name = Название зала (например, «Большой зал»):
step-welcome-goal = Что делаем сегодня?
step-welcome-goal-full = Полная настройка: замер, предложения и проверка
step-welcome-goal-verify = Только проверить, как звучит сейчас
step-welcome-goal-sub = Только сабвуфер
step-welcome-goal-report = Только отчёт по прошлой сессии

step-mac-title = Проверка Mac
step-mac-what = Сначала проверим этот Mac: звуковое подключение к пульту и нужные настройки.
step-mac-dev-safe = Режим разработки: используются только эмулятор пульта и модель зала.
step-mac-no-audio = Не найдено звуковое устройство со входами и выходами.
step-mac-audio-problems = Звуковое устройство { $name }: { $problems } (нужно 32 канала на 48 кГц).
step-mac-audio-ok = Звуковое устройство { $name } готово.
step-mac-fix-audio = Подключите USB-кабель от пульта и запустите снова. В «Настройке Audio-MIDI» карта должна работать на 48 кГц.

step-connect-title = Пульт
step-connect-searching = Ищу пульт в сети…
step-connect-none = Пульт не ответил.
step-connect-enter-ip = Введите адрес пульта (на пульте: Setup → Network), например 192.168.x.x:
step-connect-found = Найден { $model } «{ $name }» по адресу { $host } (прошивка { $firmware }). Это ваш пульт?
step-connect-option = { $model } «{ $name }», { $host }
step-connect-several = Ответили несколько пультов. Какой ваш?
step-connect-fix = Проверьте, что Mac и пульт в одной сети, и выключите VPN. В macOS разрешите этот терминал: Системные настройки → Конфиденциальность и безопасность → Локальная сеть.
step-connect-link = Связь проверена: потеряно { $loss_pct } %, ответ за { $median_ms } мс.
step-connect-firmware-unverified = Прошивку { $firmware } с этой программой не проверяли. Программа будет только читать и подсказывать.

step-read-title = Чтение пульта
step-read-progress = Читаю пульт
step-read-done = Прочитано настроек: { $count }. Ничего не изменено.
step-read-unparsed = { $count ->
    [one] { $count } настройку не удалось разобрать; она сохранена в файле.
    [few] { $count } настройки не удалось разобрать; они сохранены в файле.
   *[many] { $count } настроек не удалось разобрать; они сохранены в файле.
}
step-read-save-scene = Сохраните сейчас сцену на пульте: SCENES → свободный слот → SAVE, имя «before-ctune-{ $date }». Так всё можно вернуть и без компьютера.

step-topology-title = Колонки
step-topology-what = Вот колонки, найденные на выходах пульта. Проверьте роли.
step-topology-correct = Всё верно?
step-topology-ask-role = { $output } (источник { $path }, имя «{ $name }»): что сюда подключено?

step-equipment-title = Оборудование
step-equipment-what = Несколько вопросов, на которые пульт не отвечает.
step-equipment-speakers = Колонки активные или пассивные с усилителем?
step-equipment-active = Активные (со встроенным усилителем)
step-equipment-passive = Пассивные с усилителем
step-equipment-hall = Размер зоны слушателей в метрах, ширина × глубина (например 14 × 22). Можно оставить пустым — возьмём стандартный план.
step-equipment-mic-count = Сколько у вас измерительных микрофонов?
step-equipment-mics = { $count ->
    [one] { $count } микрофон
    [few] { $count } микрофона
   *[many] { $count } микрофонов
}
step-equipment-mic-model = Какая модель?
step-equipment-model = { $model }

step-mics-title = Микрофоны
step-mics-what = Сейчас найдём, на каком входе пульта каждый измерительный микрофон.
step-mics-plug = Подключите микрофоны в стейджбокс или в пульт и включите на этих входах +48V. Сама программа фантомное питание не включает.
step-mics-scratch = Нажмите Enter, подойдите к микрофону { $number } и слегка поскребите ногтем по его сетке. Слушаем 15 секунд.
step-mics-found = Микрофон { $number } — на USB-входе { $usb }.
step-mics-nothing = Ничего не услышал. Проверьте +48V, кабель и вход, затем ещё раз.
step-mics-ambiguous = Отреагировали сразу несколько входов. Трогайте только один микрофон.
step-mics-already-mapped = Это USB-вход { $usb }, он уже найден. Поскребите следующий микрофон.
step-mics-stop-here = Продолжить с найденными микрофонами
step-mics-not-preamp = { $mic } идёт не с микрофонного входа. Проверьте маршрутизацию карты.
step-mics-phantom-off = { $mic }: на предусилителе (headamp { $headamp }) выключено +48V. Включите.
step-mics-console-checks = Сделайте это на пульте и нажмите Enter:

step-path-title = Путь воспроизведения
step-path-no-playback = Ни один канал не играет звук компьютера. Назначьте USB-выходы карты 1 и 2 на два канала и отправьте их в Main LR.
step-path-assign = Канал { $channel } играет звук компьютера: включите его и назначьте на Main LR.
step-path-console-checks = Сделайте это на пульте и нажмите Enter:
step-path-test = Тихая проверка: короткий тон и немного шума на −40 dBFS, около 3 секунд.
step-path-latency = { $ms } мс
step-path-h-latency = Задержка
step-path-heard = Все микрофоны слышат колонки.
step-path-not-heard = Некоторые микрофоны ничего не слышат. Проверьте, что каналы воспроизведения и Main LR подняты и не замьючены.

step-level-title = Уровень
step-level-what = Сейчас подберём комфортную громкость замера — по микрофонам.
step-level-quiet = Пожалуйста, 3 секунды тишины в зале: слушаем фоновый шум.
step-level-noise-hints = Фоновый шум может помешать замеру:
step-level-ramp = Розовый шум (ровное шипение) начнётся очень тихо и будет медленно нарастать, пока микрофон не услышит достаточно. Затем несколько коротких тестовых свипов (тон, плавно идущий от низких к высоким) подберут уровень замера.
step-level-ramp-target = Уровень найден: { $level } dBFS на первом микрофоне.
step-level-ramp-above-target = Уровень найден, чуть высоковат: { $level } dBFS на первом микрофоне.
step-level-ramp-output-limit = Выход компьютера дошёл до безопасного максимума раньше, чем микрофон услышал достаточно ({ $level } dBFS).
step-level-ramp-clip = Микрофон перегрузился: убавьте его gain ({ $level } dBFS).
step-level-ramp-jump = Уровень резко подскочил ({ $level } dBFS): кто-то двигал фейдер?
step-level-ramp-no-response = Звук стал громче, а микрофон этого не услышал ({ $level } dBFS). Не тот вход, отключён или звук не идёт из колонок?
step-level-ramp-user = Остановлено.
step-level-ramp-ended = Звук прекратился раньше, чем нашёлся уровень.
step-level-not-target = Уровень не идеальный. Устраните причину и повторите или продолжайте как есть.
step-level-continue = Продолжить с этим уровнем
step-level-sweep = Уровень замера: { $level } dBFS.
step-level-sweep-output-limit = Даже на безопасном максимуме микрофон слышит мало. Прибавьте gain микрофона на пульте.
step-level-sweep-too-loud-at-min = Слишком громко даже на минимальном уровне: убавьте gain микрофона или фейдеры Main.
step-level-sweep-no-response = Микрофон не реагирует на тестовый сигнал. Проверьте вход и кабель.

step-calibration-title = Калибровка микрофонов
step-calibration-reuse = Эти микрофоны недавно калибровали; используем ту калибровку.
step-calibration-what = Сравним микрофоны между собой, чтобы их небольшие различия не приняли за различия в зале.
step-calibration-place = Поставьте все микрофоны рядом: капсюли в пределах 10 см, на одной высоте, смотрят в одну сторону, в 3–5 м от колонок.
step-calibration-sweeps = Два тестовых свипа (тон, плавно идущий от низких к высоким), около 20 секунд.
step-calibration-bad-sweeps = Калибровочные свипы получились нечистыми (перегрузка, шум или движение). Проверьте gain и тишину и повторите.
step-calibration-sensitivity = { $db } дБ
step-calibration-h-sens = Чувствительность
step-calibration-done = Калибровка готова.
step-calibration-polarity-ambiguous = У половины микрофонов обратная полярность: проверьте кабели всех микрофонов.
step-calibration-pair-differs = Один из двух микрофонов отличается, но какой — не понять. Проверьте оба.
step-calibration-exclude = { $count ->
    [one] { $mics } отличается от остальных. Исключить его из замера?
   *[other] { $mics } отличаются от остальных. Исключить их из замера?
}

step-points-title = Замер
step-points-what = Сейчас измерим зал в нескольких местах, где сидят люди.
step-points-plan = Мест для замера: { $count }.
step-points-place = { $mic } → место { $point } из { $total } ({ $place })
step-points-height = Капсюль на высоте ушей сидящего человека (1,2 м), направлен на сцену.
step-points-bad = { $count ->
    [one] { $count } место измерилось нечисто:
    [few] { $count } места измерились нечисто:
   *[many] { $count } мест измерились нечисто:
}
step-points-keep = Оставить как есть
step-points-done = Измерено мест: { $count }.

step-results-title = Итоги
step-results-no-points = Пока нет измеренных мест.
step-results-what = Что показал замер в { $count } местах:
step-results-next-milestones = Предложения изменений (кроссовер, выравнивание саба, EQ системы) и их применение появятся в следующей версии. Ваш замер сохранён для них.
finding-hump = Горб около { $freq_hz } Гц (+{ $gain_db } дБ): голос может бубнить.
finding-rt60-dry = Зал скорее сухой (RT60 { $rt60_s } с): хорошо для речи.
finding-rt60-good = Зал умеренно гулкий (RT60 { $rt60_s } с): хорошо и для речи, и для музыки.
finding-rt60-boomy = Зал гулкий (RT60 { $rt60_s } с): речь хуже разборчива. EQ это не исправит — помогут шторы и поглотители.
finding-rt60-treatment = Зал очень гулкий (RT60 { $rt60_s } с): для разборчивой речи нужна акустическая обработка. EQ это не исправит.
finding-spread-small = Звук на разных местах похож (разброс { $spread_db } дБ).
finding-spread-large = Звук на разных местах сильно отличается (разброс { $spread_db } дБ): здесь направление колонок важнее EQ.

step-finish-title = Что дальше
step-finish-what = На сегодня всё. Перед уходом:
step-finish-listen = На ближайшем служении послушайте речь и музыку с нескольких мест.
step-finish-mics = { $count ->
    [one] Отключите измерительный микрофон, затем снимите mute с его канала, если мьютили.
    [few] Отключите { $count } измерительных микрофона, затем снимите mute с их каналов, если мьютили.
   *[many] Отключите { $count } измерительных микрофонов, затем снимите mute с их каналов, если мьютили.
}
step-finish-saved = Всё сохранено в { $path }.
step-connect-not-this-one = Тогда введите адрес вашего пульта.
step-equipment-hall-invalid = Такой размер не подходит: нужны два числа от 3 до 200 метров, сначала ширина.
step-equipment-model-other = Другая модель
step-mics-listening = Слушаем микрофон { $number }: осталось { $seconds } с
step-mics-mute-channels = { $count ->
    [one] Выключите (mute) канал { $channels }: он слушает измерительный микрофон, а микрофоны не должны попасть в колонки (будет завязка).
   *[other] Выключите (mute) каналы { $channels }: они слушают измерительные микрофоны, а микрофоны не должны попасть в колонки (будет завязка).
}
step-mics-rereading = Проверяю пульт перед первым звуком
step-mics-still-open = { $count ->
    [one] Канал { $channels } всё ещё отправляет измерительный микрофон в колонки. Выключите его (mute) на пульте и нажмите Enter.
   *[other] Каналы { $channels } всё ещё отправляют измерительный микрофон в колонки. Выключите их (mute) на пульте и нажмите Enter.
}
step-mics-why-mute = Иначе тестовый сигнал через микрофон даст завязку.
step-path-what = Сейчас проверим, что звук компьютера идёт в колонки зала, а не в мониторы.
step-path-close-sends = { $count ->
    [one] Канал { $channels }: уберите посылы в шины { $buses }, чтобы тестовый сигнал не попал в мониторы на сцене и в трансляцию.
   *[other] Каналы { $channels }: уберите посылы в шины { $buses }, чтобы тестовый сигнал не попал в мониторы на сцене и в трансляцию.
}
step-points-sweeps = Два тестовых свипа, около 15 секунд. После Enter у вас 3 секунды, чтобы отойти хотя бы на 2 м.
point-front-left = передние ряды, слева
point-front-centre = передние ряды, по центру
point-front-right = передние ряды, справа
point-middle-left = середина, слева
point-middle-right = середина, справа
point-back-left = задние ряды, слева
point-back-centre = задние ряды, по центру
point-back-right = задние ряды, справа
point-number = место { $number }
step-finish-undo-by-hand = Верните то, что сегодня меняли на пульте руками (mute, посылы).
step-points-put = { $count ->
    [one] Поставьте микрофон сюда:
   *[other] Поставьте микрофоны в эти места:
}
