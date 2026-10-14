## Роли колонок, схемы и замечания по топологии

role-top-l = портал, левый
role-top-r = портал, правый
role-top-mono = портал (моно)
role-sub = сабвуфер
role-fill = фронтфил
role-delay = задержанные / балкон
role-zone = колонки в другом помещении
role-monitor = монитор на сцене
role-unused = не колонки

variant-a-tops-and-sub-on-own-paths = Схема A: порталы и саб на своих матрицах или шинах.
variant-b-tops-on-main-sub-separate = Схема B: порталы прямо с Main LR, саб отдельно.
variant-c-tops-only = Схема C: порталы без отдельного пути саба.
variant-sub-shares-main = Саб висит на Main LR вместе с порталами: кроссовер настроить нельзя.
variant-unknown = Порталы не найдены.

topology-h-output = Выход
topology-h-path = Источник
topology-h-name = Имя
topology-h-role = Роль (определена)
topology-h-confidence = Уверенность
topology-name = { $name }
topology-confidence = { $pct } %

issue-several-subs = Несколько путей похожи на саб ({ $paths }). Подтвердите, какой настоящий.
issue-sub-pair = Сабы стоят парой ({ $paths }); они настраиваются как один.
issue-sub-on-mono-suggest-only = Саб на Main Mono. Запись туда ещё не проверена: программа покажет, что выставить руками.
issue-sub-on-bus-suggest-only = Саб на шине { $bus }. Запись в шины ещё не проверена: программа покажет, что выставить руками.
issue-playback-not-sent-to-sub-bus = Каналы воспроизведения ничего не посылают в шину саба { $bus }: во время замера саб будет молчать.
issue-sub-not-fed = Матрица саба { $matrix } ни от чего не получает сигнал.
issue-sub-fed-by-main-and-mono = Матрица саба { $matrix } получает и Main LR, и Mono: каналы на Mono попадут в саб дважды.
issue-main-hpf-feeds-sub = На Main LR стоит срез низов { $freq_hz } Гц, а саб питается от Main: саб недополучит низы.
issue-crossover-hpf-suggest-only = Порталы идут прямо с Main LR. Не ставьте ФВЧ кроссовера на Main LR (саб тоже питается от него): его ставят в процессоре колонок, либо порталы переводят на матрицы.
issue-main-and-matrix-tops = На выходах и Main L/R ({ $mains }), и матрицы порталов ({ $matrices }). Вероятно, Main L/R идут на запись или в фойе. Подтвердите.
issue-no-sub-path-ask = Отдельного пути саба нет. Если сабвуфер есть, он, вероятно, висит на Main LR вместе с порталами — тогда кроссовер настроить нельзя.
issue-other-speakers-audible = Путь «{ $path }» включён и будет звучать во время замера. Эти колонки в этой версии не настраиваются.
issue-other-speakers-found = Найден путь «{ $path }». Эти колонки в этой версии не настраиваются.
issue-zone-will-hear-sweep = { $path } питает колонки в другом помещении: там услышат тестовый сигнал. Предупредите людей или замьютьте этот путь.
issue-monitors-on-outputs = Мониторы на выходах: { $paths }. Тестовый сигнал не должен в них попасть.
issue-side-conflict = Лево и право не совпадают по имени и по связке ({ $path }). Проверьте.
issue-existing-alignment = На путях колонок уже стоят задержка или полярность: { $items }.
issue-lcr-not-supported = Схема LCR с центральным кластером в этой версии не настраивается.
issue-confirm-roles = { $count ->
    [one] { $count } выход определён неуверенно: подтвердите его роль.
    [few] { $count } выхода определены неуверенно: подтвердите их роли.
   *[many] { $count } выходов определены неуверенно: подтвердите их роли.
}
