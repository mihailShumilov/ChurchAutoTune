## Speaker roles, layouts and topology issues

role-top-l = main speaker, left
role-top-r = main speaker, right
role-top-mono = main speaker (mono)
role-sub = subwoofer
role-fill = front fill
role-delay = delay / balcony speakers
role-zone = speakers in another room
role-monitor = stage monitor
role-unused = not a speaker

variant-a-tops-and-sub-on-own-paths = Layout A: main speakers and subwoofer on their own matrices or buses.
variant-b-tops-on-main-sub-separate = Layout B: main speakers straight from Main LR, subwoofer separate.
variant-c-tops-only = Layout C: main speakers without a subwoofer path.
variant-sub-shares-main = The subwoofer shares Main LR with the main speakers: no crossover is possible.
variant-unknown = No main speakers were found.

topology-h-output = Output
topology-h-path = Fed by
topology-h-name = Name
topology-h-role = Role (detected)
topology-h-confidence = Sure
topology-name = { $name }
topology-confidence = { $pct } %

issue-several-subs = Several paths look like a subwoofer ({ $paths }). Confirm which one is real.
issue-sub-pair = The subwoofers stand as a pair ({ $paths }); they are tuned as one.
issue-sub-on-mono-suggest-only = The subwoofer is on Main Mono. Writing there is not verified yet: you will get the values to set by hand.
issue-sub-on-bus-suggest-only = The subwoofer is on bus { $bus }. Writing to buses is not verified yet: you will get the values to set by hand.
issue-playback-not-sent-to-sub-bus = The playback channels send nothing to the subwoofer bus { $bus }: the subwoofer would stay silent during the measurement.
issue-sub-not-fed = The subwoofer matrix { $matrix } gets no signal from anywhere.
issue-sub-fed-by-main-and-mono = The subwoofer matrix { $matrix } gets both Main LR and Mono: channels on Mono reach the subwoofer twice.
issue-main-hpf-feeds-sub = Main LR has a low cut at { $freq_hz } Hz and feeds the subwoofer: the subwoofer misses its lows.
issue-crossover-hpf-suggest-only = The main speakers come straight from Main LR. Do not put the crossover high-pass on Main LR (the subwoofer is fed from it too): it belongs in the speakers' own processing, or move the main speakers to matrices.
issue-main-and-matrix-tops = Main L/R ({ $mains }) and main-speaker matrices ({ $matrices }) are both on outputs. Main L/R probably feed a recorder or the foyer. Please confirm.
issue-no-sub-path-ask = There is no separate subwoofer path. If you have a subwoofer, it probably shares Main LR with the main speakers; then the crossover cannot be tuned.
issue-other-speakers-audible = The path “{ $path }” is switched on and will sound during the measurement. These speakers are not tuned in this version.
issue-other-speakers-found = The path “{ $path }” was found. These speakers are not tuned in this version.
issue-zone-will-hear-sweep = { $path } feeds speakers in another room: people there will hear the test signal. Warn them or mute that path.
issue-monitors-on-outputs = Stage monitors on outputs: { $paths }. The test signal must not reach them.
issue-side-conflict = Left and right do not agree between the name and the stereo link ({ $path }). Please check.
issue-existing-alignment = Delay or polarity is already set on speaker paths: { $items }.
issue-lcr-not-supported = An LCR system with a centre cluster is not tuned in this version.
issue-confirm-roles = { $count ->
    [one] One output is uncertain: please confirm its role.
   *[other] { $count } outputs are uncertain: please confirm their roles.
}
