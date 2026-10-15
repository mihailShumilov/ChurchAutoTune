## The session report (SPEC §10): report.html, report.md and the text for a chat.

report-title = Sound system tuning report: { $venue }
report-subtitle = Measured on { $created }. The program changes the console only after you confirm; this report says what was measured, what was changed and what to do next.
report-contents = Contents
report-none = Nothing was measured for this part.
report-place = { $name } ({ $where })
report-date = { $day } { $month } { $year }, { $time }
report-month-1 = January
report-month-2 = February
report-month-3 = March
report-month-4 = April
report-month-5 = May
report-month-6 = June
report-month-7 = July
report-month-8 = August
report-month-9 = September
report-month-10 = October
report-month-11 = November
report-month-12 = December
report-decimal-separator = .
report-kilo = k

report-section-summary = Summary
report-section-system = The system
report-section-quality = How well each place was measured
report-section-response = How the hall sounds (frequency response)
report-section-junction = Where the subwoofer meets the main speakers
report-section-decay = Reverberation
report-section-changes = What was changed on the console
report-section-verify = The check measurement
report-section-audit = Advice on the input channels
report-section-acoustics = What the room itself could use
report-section-checklist = What to do next
report-section-appendix = Technical details

report-summary-verdict-better = The check measurement says: the sound got better.
report-summary-verdict-no-change = The check measurement says: no clear difference at the measured places. You may keep the changes and listen at the next service; how to put the old values back is in “What to do next”.
report-summary-verdict-worse = The check measurement says: the sound got worse. Put the old values back: the command is in “What to do next”.
report-summary-reverted-after-check = The changes have already been put back: the console is as it was.
report-summary-applied-applied = Changes were written to the console, but there was no check measurement afterwards. Listen at the next service; if it is worse, put the old values back (see “What to do next”).
report-summary-applied-nothing = Nothing was written to the console: this report only describes the hall.
report-summary-applied-reverted = The changes were written and then put back: the console is as it was.
report-summary-measured-only = The hall was measured; nothing was written to the console.
report-summary-next = The scene on the console is not saved yet. What to do now is in “What to do next”.
report-summary-findings-before = What the first measurement (before any change) showed:

report-column-what = What
report-column-value = Value
report-column-point = Place
report-column-verdict = Result
report-column-snr = Signal above noise
report-column-pnr = Peak above noise
report-column-peak = Loudest sample
report-column-repeat = Two takes differ by
report-column-why = Why
report-column-octave = Octave
report-column-t20 = RT60 (T20)
report-column-edt = Early decay (EDT)
report-column-parameter = Console setting
report-column-before = Before
report-column-after = After

report-value-db = { $value } dB
report-value-dbfs = { $value } dBFS
report-value-s = { NUMBER($value, minimumFractionDigits: 2) } s
report-value-s-approx = ≈ { NUMBER($value, minimumFractionDigits: 2) } s
report-value-hz = { $value } Hz

report-system-console = Console
report-system-mics = Measurement microphones
report-system-variant = Layout

report-quality-what = Each place was measured with test sweeps (a sound that glides from low to high). “Signal above noise” should be at least 30 dB (20–30 dB: usable, with a warning); the two takes of a place should differ by 1 dB or less (1–2 dB: a warning).
report-quality-ok = good
report-quality-warn = usable, with a warning
report-quality-bad = not used
report-quality-calibration-none = The microphones were not calibrated against each other: small differences between places may come from the microphones, and above 10 kHz the curves are only approximate.

report-response-what = The frequency response is how loudly the system plays each frequency, from deep bass to high treble. Each line is one place; the thick line is their average. 0 dB is the level between 500 Hz and 2 kHz; above 10 kHz (the grey band) the microphone is only approximate. Humps that show at all places can be evened out with EQ; narrow dips that move from place to place come from the room and must not be boosted. The curves include the EQ the console had during the measurement.
report-figure-points = Frequency response at each place and the average.
report-figure-average = The average, the spread between places, and the target curve (the shape the tuning aims for).
report-figure-before-after = The average before the changes (dotted line) and after (solid line), with the target curve (dashed).

report-junction-not-measured = The subwoofer and the main speakers were not measured separately, so nothing is known about where they meet.
report-junction-what = The subwoofer and the main speakers were measured separately at a few places, before any change, to see how well they add up around the crossover.
report-junction-fc = Crossover frequency
report-junction-level = Subwoofer against the main speakers at the crossover
report-junction-level-same = about equal (good)
report-junction-level-louder = the subwoofer is { $db } dB louder
report-junction-level-quieter = the subwoofer is { $db } dB quieter
report-junction-delay = Which to delay for the best sum
report-junction-delay-none = neither
report-junction-delay-sub = the subwoofer, by { $ms } ms
report-junction-delay-tops = the main speakers, by { $ms } ms
report-junction-polarity = Subwoofer polarity that adds up best
report-junction-advice = These are calculations from the measurement, not console settings. The program does not write delay and polarity: set them by hand only if ChurchTune advised it during the tuning. Filters and levels come first; a delay is worth changing only if it clearly improves the sum.
report-polarity-normal = normal
report-polarity-inverted = inverted
report-figure-junction = Subwoofer alone, main speakers alone and both together around the crossover.

report-decay-what = RT60 is how long the hall rings after a sound stops; EDT is the same judged by the first moments of the decay, closer to what the ear hears. “—” means it could not be measured reliably in that octave.
report-decay-speech-dry = For speech: very clear (RT60 { $rt60_s } s at 500 Hz–1 kHz). The room adds little of its own.
report-decay-speech-good = For speech: clear (RT60 { $rt60_s } s at 500 Hz–1 kHz). Speaking close to the microphone keeps it so.
report-decay-speech-boomy = For speech: the room blurs words (RT60 { $rt60_s } s at 500 Hz–1 kHz). Louder does not make words clearer: keep the level moderate and the main speakers aimed at the people, not at the walls.
report-decay-speech-treatment = For speech: words get lost in the reverberation (RT60 { $rt60_s } s at 500 Hz–1 kHz). Changes to the room help most; speakers aimed precisely at the people, or extra delayed speakers for the back rows, help somewhat; EQ will not.
report-decay-music-dry = For music: rather dry; a little reverb from the console can help singing.
report-decay-music-good = For music: a good balance for singing and instruments.
report-decay-music-boomy = For music: full and lively; fast songs and dense arrangements can get blurred.
report-decay-music-treatment = For music: very washy. Use little or no console reverb, and keep bass and kick drum short and not too loud.
report-decay-edt-short = Early decay (EDT { $edt_s } s) is clearly shorter than RT60: at the places the speakers’ direct sound is strong, so words are clearer than RT60 alone suggests.
report-decay-edt-normal = Early decay (EDT { $edt_s } s) is close to RT60: what you hear at the places matches the reverberation time.
report-decay-edt-long = Early decay (EDT { $edt_s } s) is as long as RT60 or longer: the places get mostly reflected sound. Speakers aimed more precisely at the people help speech here more than EQ.
report-decay-bass-long = The bass rings longer than the mids (bass ratio { $ratio }): low-mid “mud” for speech and music. A broad gentle cut around 100–300 Hz makes it less muddy but does not shorten the ringing; thick absorbers or bass traps in the corners do.
report-decay-bass-ok = The bass does not ring much longer than the mids (bass ratio { $ratio }).
report-decay-limits = The hall was measured empty; with people in it the reverberation is shorter and the highs a little softer. Speech intelligibility (STI) was not measured: RT60 and EDT are only a rough guide, so judge clarity by ear at the next service. Values marked ≈ are approximate.
report-figure-decay = Reverberation time (T20) and early decay (EDT) per octave, averaged over the places.

report-changes-what = These settings were written to the console (each one was read back and confirmed).
report-changes-undo = How to put the old values back is in “What to do next”.
report-changes-none = Nothing was written to the console.
report-changes-nothing = Nothing was written to the console.
report-changes-reverted = The changes were written and then put back.
report-changes-applied = The changes were written; their list is in the undo file.
report-changes-advice = During the tuning ChurchTune also advised setting these by hand (the program does not write them):

report-verify-not-done = There was no check measurement.
report-verify-result-better = The check measurement showed: the sound got better.
report-verify-result-no-change = The check measurement showed: no clear difference at the measured places.
report-verify-result-worse = The check measurement showed: the sound got worse.
report-verify-what = The same places were measured again after the changes. “Deviation” is how far the average is from the target curve: lower is better. “Better” needs the deviation to drop by at least 0.5 dB with no place or band getting clearly worse; smaller differences are within what two measurements of the same thing differ by.
report-verify-m-before = Deviation from the target before
report-verify-m-after = Deviation from the target after
report-verify-worst = The place that measured worst before the changes
report-verify-band = Largest change for the worse in one band
report-verify-spread = Change of the spread between places (positive is worse)

report-audit-none = No advice on the input channels.
report-audit-what = Nothing here is changed by the program; try one change at a time at a rehearsal and listen.

report-acoustics-dry = The room is dry: it needs no extra absorption. Keep it so; congregational singing benefits from some liveness.
report-acoustics-good = The room is in good shape; avoid adding large bare surfaces (glass, bare walls).
report-acoustics-boomy = The room is quite reverberant. Most effective first: thick absorbing panels (10 cm or more, ideally with an air gap) on the back wall facing the speakers, then heavy curtains and upholstered seats. Carpet and thin panels absorb mainly the highs and can leave the bass booming. How much area is needed depends on the room size.
report-acoustics-treatment = The room is very reverberant: substantial absorption (panels and heavy curtains over large areas, best planned with an acoustician) would help speech much more than any console setting.
report-acoustics-spread = The sound differs a lot between places: check that the main speakers point at the people and that nothing blocks them; seats under a balcony or along the side walls may need their own speakers.
report-acoustics-reflections = General advice (not measured): if a hand clap leaves a ringing “zing” between bare parallel walls, or a speaker stands close to a hard wall, a heavy curtain or a filled bookshelf there often helps.
report-acoustics-no-eq-promise = An equaliser cannot fix echo or reverberation; it only evens out the tone. In a reverberant room a gentle low-mid cut reduces mud, but the reverberation stays as long.

report-check-hand = Put back what you changed by hand today (mutes, sends).
report-check-mics = Unplug the measurement microphones, unmute their channels if you muted them, and put the cables away.
report-check-scene = After the mutes and sends are back, save the scene on the console under a new name (for example today’s date): then this tuning is easy to get back even if someone loads another scene.
report-check-listen = At the next service, with people in the hall, listen at the back and at the sides: are words clear, is the bass not booming, is it not too sharp? During the sound check raise the preacher’s microphone carefully and listen for ringing or feedback.
report-check-undo = If it sounds worse, put the old values back: open Terminal on this Mac (connected to the console), type the command below, then save the scene again. { $command }
report-check-channels = Change the input channels only by ear, one setting at a time, during a rehearsal (see “Advice on the input channels”).

report-appendix-version = ChurchTune version
report-appendix-created = Session
report-appendix-calibration = Microphone calibration
report-appendix-none = none
report-appendix-details = Measurement details per place: “Peak above noise” should be at least 45 dB (35–45 dB: the reverberation is measured less precisely); the loudest sample should stay below −6 dBFS (at −0.5 dBFS the recording clipped and the place was not used).
report-appendix-fs-hz = Sample rate
report-appendix-sweep-s = Sweep length
report-appendix-f1-hz = Sweep starts at
report-appendix-f2-hz = Sweep ends at
report-appendix-level-dbfs = Sweep level

report-axis-hz = Hz
report-axis-db = dB
report-axis-db-normalised = dB (500–2000 Hz = 0)
report-axis-seconds = seconds
report-axis-octave = octave, Hz
report-legend-average = average
report-legend-spread = spread between places
report-legend-before = before
report-legend-after = after
report-legend-target = target
report-legend-sub = subwoofer
report-legend-tops = main speakers
report-legend-both = both together

report-chat-intro = Sound measurement of a church hall “{ $venue }” ({ $created }), made with ChurchTune.
report-chat-console = Console: { $model }; measurement microphones: { $mics }.
report-chat-roles = Speaker outputs: { $roles }
report-chat-findings = What the measurement says:
report-chat-thirds = Response in thirds of an octave (dB, 0 = level at 500–2000 Hz), at: { $thirds }
report-chat-average = Average: { $curve }
report-chat-spread = Spread between places: { $curve }
report-chat-decay = RT60 (T20) per octave, s: { $values }
report-chat-junction = Sub/main junction: crossover { $fc_hz } Hz, sub minus main { $sub_minus_tops_db } dB, best delay { $delay_ms } ms (positive: delay the sub), polarity { $polarity }.
report-chat-changes = Written to the console (before -> after):
report-chat-verify = Check measurement: { $verdict } (deviation { $before } -> { $after } dB).
report-chat-verdict-better = better
report-chat-verdict-no-change = no clear difference
report-chat-verdict-worse = worse
report-chat-question = Question: what else could we improve in this hall, and is anything above a concern?
report-verify-worst-value = { $place }: { $direction ->
    [better] better by { $delta_db } dB
    [worse] worse by { $delta_db } dB
   *[same] unchanged
}
report-verify-band-value = { $delta_db } dB around { $freq }, at { $place }
report-value-khz = { $value } kHz
report-junction-gloss = The crossover splits the sound: the low notes go to the subwoofer, the rest to the main speakers.
report-changes-types = LCut, HShv, LR24 and the like are filter type names as shown on the console; LR24 is the steep cut used for the crossover.
report-legend-crossover = crossover { $fc_hz } Hz
