## Общие слова, клавиши и проверки

common-yes = Да
common-no = Нет
common-retry = Ещё раз
common-microphone = Микрофон
common-step = Шаг { $number } из { $total }

keys-info = [Enter] дальше · [B] назад · [Q] сохранить и выйти
keys-confirm = [Enter] да · 2 или «нет» — нет · [B] назад · [Q] сохранить и выйти
keys-choose = Введите номер или нажмите [Enter] для рекомендуемого варианта
keys-input = Введите и нажмите [Enter]
keys-task = Сделайте это и нажмите [Enter] · [Q] сохранить и выйти
keys-typed = Введите слово и нажмите [Enter]

tui-keys-info = [Enter] дальше
tui-keys-choose = [↑ ↓] выбрать · [Enter] подтвердить
tui-keys-input = Введите и нажмите [Enter]
tui-keys-task = Сделайте это и нажмите [Enter]
tui-keys-typed = Введите слово и нажмите [Enter]
tui-keys-back = [Esc] назад
tui-keys-quit = [Ctrl+Q] сохранить и выйти

check-ok = ✓ ОК
check-warn = ! Проверьте
check-fail = ✗ Проблема
check-result = Итог
check-what = Что

sound-warning = Сейчас прозвучит тестовый сигнал. Рядом с колонками не должно быть людей с чувствительным слухом. Снимите наушники.
sound-stop-keys = Ctrl+C или пробел сразу выключают звук.
sound-stop-ctrl-c = Ctrl+C сразу выключает звук.

gate-clipping = Микрофон перегружен: убавьте его gain или играйте тише.
gate-hot = Почти перегрузка: чуть убавьте gain.
gate-quiet = Очень тихо: проверьте фантомное питание (+48V) и gain.
gate-low-snr = В зале слишком шумно для этого уровня: попросите тишины или сделайте чуть громче.
gate-low-pnr = Результат слишком шумный: повторите в тишине.
gate-xrun = Звуковой поток прерывался: закройте другие программы и повторите.
gate-unsteady = Два свипа отличаются: что-то сдвинулось или шумело. Повторите.

noise-hum = { $mic }: гул сети (диммеры, LED-драйверы). Выключите свет на сцене.
noise-rumble = { $mic }: низкочастотный шум (вентиляция, кондиционер). Выключите, если можно.
noise-loud = { $mic }: в зале шумно (разговоры, шаги, улица). Попросите тишины.
common-recommended = (рекомендуется)
common-default = Enter оставит: { $value }
common-save-quit = Сохранить и выйти
common-mic = Микрофон { $number } (USB-вход { $usb })
common-mic-number = Микрофон { $number }
common-dbfs = { $level } dBFS
plain-invalid-choice = Введите номер от 1 до { $count }.
sound-play = Включить звук
sound-later = Пока нет
sound-playing = Звучит тестовый сигнал · [пробел] или [Ctrl+C] — остановить
sound-playing-plain = Звучит тестовый сигнал. Ctrl+C — остановить.
sound-countdown = Отойдите от микрофонов: звук начнётся через 3 секунды
sound-stopped = Звук остановлен.
session-saved-resume = Сессия сохранена. Продолжить позже: ctune resume
tui-quit-pending = Останавливаю. Сессия сохранится на следующем вопросе.
audio-problem-channels = меньше 32 каналов
audio-problem-sample-rate = работает не на 48 кГц
signal-off = выключено
signal-main-l = Main L
signal-main-r = Main R
signal-mono = Main Mono
signal-bus = шина { $number }
signal-matrix = матрица { $number }
signal-direct-channel = прямой выход канала { $number }
signal-direct-aux = прямой выход aux-входа { $number }
signal-direct-fx = прямой выход возврата эффекта { $number }
signal-monitor = мониторный выход
signal-talkback = talkback
signal-unknown = неизвестный источник
output-main = выход { $number }
output-aux = aux-выход { $number }
alignment-delay = { $where }: задержка { $ms } мс
alignment-polarity = { $where }: перевёрнута полярность
signal-main-lr = Main LR

# The self-check of the installed program
selftest-start = Самопроверка ChurchTune { $version }: весь мастер проходит на встроенном в программу эмуляторе пульта и на имитации зала. Настоящий пульт и звуковая карта не используются, ничего не звучит. Это займёт не больше минуты.
selftest-ok = ✓ { $what }
selftest-failed = ✗ { $what }
selftest-what-wizard = Мастер прошёл все шаги.
selftest-what-junction = Сабвуфер и основные колонки измерены по отдельности.
selftest-what-apply = Изменения записаны в эмулятор и проверены.
selftest-what-verify = Проверочный замер сделан.
selftest-what-report = Отчёт записан.
selftest-error = Самопроверка остановилась с ошибкой ({ $error }).
selftest-passed = Самопроверка пройдена за { $seconds } с. ChurchTune установлен правильно.
selftest-not-passed = Самопроверка не пройдена. Пожалуйста, создайте issue на GitHub и вставьте туда этот вывод.
selftest-kept = Файлы самопроверки лежат в { $path }
