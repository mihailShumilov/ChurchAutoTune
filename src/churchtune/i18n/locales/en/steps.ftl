## Wizard steps. The first paragraph of every screen says what happens now.

step-welcome-title = Welcome
step-welcome-what = We will measure your hall and tune the sound system on the console. It takes about an hour in an empty hall.
step-welcome-venue = Which hall is this?
step-welcome-venue-known = { $venue } (measured before)
step-welcome-venue-new = A new hall
step-welcome-venue-name = Name of the hall (for example, "Main hall"):
step-welcome-goal = What do you want to do today?
step-welcome-goal-full = Full setup: measure, propose and check
step-welcome-goal-verify = Only check how it sounds now
step-welcome-goal-sub = Only the subwoofer
step-welcome-goal-report = Only a report of the last session

step-mac-title = Mac check
step-mac-what = First we check this Mac: the audio connection to the console and the settings it needs.
step-mac-dev-safe = Development mode: only the console emulator and the simulated hall are used.
step-mac-no-audio = No audio device with inputs and outputs was found.
step-mac-audio-problems = Audio device { $name }: { $problems } (32 channels at 48 kHz are needed).
step-mac-audio-ok = Audio device { $name } is ready.
step-mac-fix-audio = Connect the console's USB cable, then start again. In Audio MIDI Setup the card must run at 48 kHz.

step-connect-title = Console
step-connect-searching = Looking for the console on the network…
step-connect-none = No console answered.
step-connect-enter-ip = Type the console's address (Setup → Network on the console), for example 192.168.x.x:
step-connect-found = Found: { $model } "{ $name }" at { $host } (firmware { $firmware }). Is this your console?
step-connect-option = { $model } "{ $name }" at { $host }
step-connect-several = Several consoles answered. Which one is yours?
step-connect-fix = Check that the Mac and the console are on the same network, and turn the VPN off. On macOS allow this terminal in System Settings → Privacy & Security → Local Network.
step-connect-link = Connection checked: { $loss_pct } % lost, { $median_ms } ms per answer.
step-connect-firmware-unverified = Firmware { $firmware } was not tested with this program. It will only read and suggest.

step-read-title = Reading the console
step-read-progress = Reading the console
step-read-done = Read { $count } settings. Nothing was changed.
step-read-unparsed = { $count ->
    [one] One setting could not be understood; it is kept in the file.
   *[other] { $count } settings could not be understood; they are kept in the file.
}
step-read-save-scene = Save a scene on the console now: SCENES → a free slot → SAVE, name it "before-ctune-{ $date }". It brings everything back without a computer.

step-topology-title = Speakers
step-topology-what = These are the speakers found on the console outputs. Check the roles.
step-topology-correct = Is this right?
step-topology-ask-role = { $output } (fed by { $path }, name "{ $name }"): what is connected here?

step-equipment-title = Equipment
step-equipment-what = A few questions the console cannot answer.
step-equipment-speakers = Are the speakers active (powered) or passive with an amplifier?
step-equipment-active = Active (powered speakers)
step-equipment-passive = Passive with an amplifier
step-equipment-hall = Size of the listening area in metres, width × depth (for example 14 × 22). Leave empty to use a standard plan.
step-equipment-mic-count = How many measurement microphones do you have?
step-equipment-mics = { $count ->
    [one] 1 microphone
   *[other] { $count } microphones
}
step-equipment-mic-model = Which model?
step-equipment-model = { $model }

step-mics-title = Microphones
step-mics-what = Now we find which console input each measurement microphone is on.
step-mics-plug = Plug the microphones into the stage box or the console and switch +48V on for those inputs. The program never switches phantom power itself.
step-mics-scratch = Press Enter, walk to microphone { $number } and lightly scratch its grille with a fingernail. We listen for 15 seconds.
step-mics-found = Microphone { $number } is on USB input { $usb }.
step-mics-nothing = Nothing was heard. Check +48V, the cable and the input, then try again.
step-mics-ambiguous = Several inputs reacted at once. Touch only one microphone.
step-mics-already-mapped = That was USB input { $usb }, already found. Scratch the next microphone.
step-mics-stop-here = Continue with the microphones found so far
step-mics-not-preamp = { $mic } does not come from a microphone input. Check the card routing.
step-mics-phantom-off = { $mic }: +48V is off on its preamp (headamp { $headamp }). Switch it on.
step-mics-console-checks = Please do this on the console, then press Enter:

step-path-title = Playback path
step-path-no-playback = No channel plays the computer's sound. Patch the USB card outputs 1 and 2 to two channels and assign them to Main LR.
step-path-assign = Channel { $channel } plays the computer's sound: switch it on and assign it to Main LR.
step-path-console-checks = Please do this on the console, then press Enter:
step-path-test = A quiet test: a short tone and some noise at −40 dBFS, about 3 seconds.
step-path-latency = { $ms } ms
step-path-h-latency = Delay
step-path-heard = Every microphone hears the speakers.
step-path-not-heard = Some microphones hear nothing. Check that the playback channels and Main LR are up and not muted.

step-level-title = Level
step-level-what = Now we find a comfortable level for the measurement, using the microphones.
step-level-quiet = Please keep the hall quiet for 3 seconds while we listen to the background noise.
step-level-noise-hints = The background noise could disturb the measurement:
step-level-ramp = Pink noise (a steady hiss) will start very quietly and rise slowly until the microphone hears enough. Then a few short test sweeps (a tone gliding from low to high) find the measurement level.
step-level-ramp-target = Level found: { $level } dBFS at the first microphone.
step-level-ramp-above-target = Level found, slightly high: { $level } dBFS at the first microphone.
step-level-ramp-output-limit = The computer's output reached its safe maximum before the microphone heard enough ({ $level } dBFS).
step-level-ramp-clip = A microphone clipped: turn its gain down ({ $level } dBFS).
step-level-ramp-jump = The level jumped suddenly ({ $level } dBFS): did someone move a fader?
step-level-ramp-no-response = The sound got louder but the microphone did not follow ({ $level } dBFS). Wrong input, unplugged, or no sound from the speakers?
step-level-ramp-user = Stopped.
step-level-ramp-ended = The sound stopped before a level was found.
step-level-not-target = The level is not ideal. Fix the cause and try again, or continue anyway.
step-level-continue = Continue with this level
step-level-sweep = Measurement level: { $level } dBFS.
step-level-sweep-output-limit = Even at the safe maximum the microphone hears little. Raise the microphone gain on the console.
step-level-sweep-too-loud-at-min = Too loud even at the lowest level: turn the microphone gain or the main faders down.
step-level-sweep-no-response = The microphone does not follow the test signal. Check the input and the cable.

step-calibration-title = Microphone calibration
step-calibration-reuse = These microphones were calibrated recently; that calibration is used.
step-calibration-what = Your microphones are compared with each other so their small differences do not count as differences in the hall.
step-calibration-place = Put all microphones side by side: capsules within 10 cm, same height, pointing the same way, 3–5 m from the speakers.
step-calibration-sweeps = Two test sweeps (a tone that glides from low to high), about 20 seconds.
step-calibration-bad-sweeps = The calibration sweeps were not clean (clipping, noise or movement). Check the gain and the quiet, then repeat.
step-calibration-sensitivity = { $db } dB
step-calibration-h-sens = Sensitivity
step-calibration-done = Calibration done.
step-calibration-polarity-ambiguous = Half of the microphones have the opposite polarity: check every microphone cable.
step-calibration-pair-differs = One of the two microphones differs, but which one cannot be told. Check both.
step-calibration-exclude = { $count ->
    [one] { $mics } differs from the others. Leave it out of the measurement?
   *[other] { $mics } differ from the others. Leave them out of the measurement?
}

step-points-title = Measurement
step-points-what = Now we measure the hall at several places where people sit.
step-points-plan = { $count } places will be measured.
step-points-place = { $mic } → place { $point } of { $total } ({ $place })
step-points-height = Capsule at ear height of a seated person (1.2 m), pointing at the stage.
step-points-bad = { $count ->
    [one] One place was not measured cleanly:
   *[other] { $count } places were not measured cleanly:
}
step-points-keep = Keep it anyway
step-points-done = { $count } places measured.

step-results-title = Results
step-results-no-points = There are no measured places yet.
step-results-what = What the measurement at { $count } places shows:
step-results-next-apply = Your choices are saved. The next step shows exactly what will change on the console and asks before it writes anything.
finding-hump = A hump around { $freq_hz } Hz (+{ $gain_db } dB above the target): voices may sound boomy.
finding-hump-sharp = A hump around { $freq_hz } Hz (+{ $gain_db } dB above the target): the sound may be sharp or harsh.
finding-hump-boxy = A hump around { $freq_hz } Hz (+{ $gain_db } dB above the target): voices may sound boxy or nasal.
finding-rt60-dry = The hall is rather dry (RT60 { $rt60_s } s): good for speech.
finding-rt60-good = The hall reverberates moderately (RT60 { $rt60_s } s): good for speech and music.
finding-rt60-boomy = The hall is reverberant (RT60 { $rt60_s } s): speech gets less clear. EQ does not fix this; enough absorption does (curtains, panels — usually tens of square metres).
finding-rt60-treatment = The hall is very reverberant (RT60 { $rt60_s } s): acoustic treatment is needed for clear speech. EQ cannot fix this.
finding-spread-small = The sound is similar across the seats (spread { $spread_db } dB).
finding-spread-large = The sound differs a lot between seats (spread { $spread_db } dB): speaker aiming matters more than EQ here.

step-finish-title = Next steps
step-finish-what = Done for today. Before you leave:
step-finish-listen = At the next service, listen to the speech and the music from several seats.
step-finish-mics = { $count ->
    [one] Unplug the measurement microphone, then unmute its channel if you muted it.
   *[other] Unplug the { $count } measurement microphones, then unmute their channels if you muted them.
}
step-finish-saved = Everything is saved in { $path }.
step-finish-report = The report is ready. It opens in any browser, without internet, and prints well.{"\u000A"}{ $path }{"\u000A"}To open it now, type in Terminal: { $command }
step-finish-report-failed = The report could not be written. Everything measured is saved. Check that the disk has free space, then type in Terminal: ctune report
step-connect-not-this-one = Then type the address of your console.
step-equipment-hall-invalid = That is not a size we can use: two numbers between 3 and 200 metres, width first.
step-equipment-model-other = Another model
step-mics-listening = Listening for microphone { $number }: { $seconds } s left
step-mics-mute-channels = { $count ->
    [one] Mute channel { $channels }: it listens to a measurement microphone, and the microphones must never reach the speakers (feedback).
   *[other] Mute channels { $channels }: they listen to the measurement microphones, and the microphones must never reach the speakers (feedback).
}
step-mics-rereading = Checking the console before the first sound
step-mics-still-open = { $count ->
    [one] Channel { $channels } still sends a measurement microphone to the speakers. Mute it on the console, then press Enter.
   *[other] Channels { $channels } still send a measurement microphone to the speakers. Mute them on the console, then press Enter.
}
step-mics-why-mute = Otherwise the test sound would feed back through the microphone.
step-path-what = Now we check that the computer's sound goes to the hall speakers, not to the monitors.
step-path-close-sends = { $count ->
    [one] Channel { $channels }: turn down the sends to buses { $buses }, so the test sound stays out of the stage monitors and the stream.
   *[other] Channels { $channels }: turn down the sends to buses { $buses }, so the test sound stays out of the stage monitors and the stream.
}
step-points-sweeps = Two test sweeps, about 15 seconds. After Enter you have 3 seconds to step away at least 2 m.
point-front-left = front rows, left
point-front-centre = front rows, centre
point-front-right = front rows, right
point-middle-left = middle, left
point-middle-right = middle, right
point-back-left = back rows, left
point-back-centre = back rows, centre
point-back-right = back rows, right
step-finish-undo-by-hand = Put back what you changed by hand today (mutes, sends).
step-points-put = { $count ->
    [one] Put the microphone here:
   *[other] Put the microphones at these places:
}
step-points-map = Plan of the hall: the stage is the solid bar at the top, the numbers are the places. Left and right as you face the stage.
point-middle-centre = middle, centre
proposal-what = { $count ->
    [one] The measurement suggests { $count } package of changes. It shows what changes, why, and what to expect; add it to the plan or leave it out.
   *[other] The measurement suggests { $count } packages of changes. Each shows what changes, why, and what to expect; add it to the plan or leave it out.
}
proposal-nothing = Nothing to change: the system is already close to the target.
package-crossover = Crossover between the subwoofer and the main speakers
package-crossover-why = The low notes go only to the subwoofer and the rest only to the main speakers, so they stop cancelling each other where they overlap.
package-alignment = Subwoofer alignment
package-alignment-why = A small delay (and maybe a polarity flip) makes the subwoofer and the main speakers arrive together, so they add up instead of cancelling.
package-sub-level = Subwoofer level
package-sub-level-why = The subwoofer and the main speakers should be about equally loud where they meet. Many people like the subwoofer 2–4 dB louder for music: adjust by ear afterwards.
package-system-eq = System EQ
package-system-eq-why = Gentle corrections for what the hall does to the sound in most seats. Narrow dips in single seats are left alone: EQ cannot fix them.
package-suggest-only = This one is advice only: the program will show the values and you set them by hand.
package-question = Add “{ $package }” to the change plan?
package-keep = Add to plan
package-keep-advice = Add to plan as advice
package-skip = Leave it out
risk-low = Risk: low. Small changes, easy to undo.
risk-medium = Risk: medium. The balance of the system changes audibly; listen afterwards.
risk-unverified = Risk: this setting has not been checked on a real console yet, so the program never writes it itself.
change-what = Where
change-before = Now
change-after = Proposed
eq-path-band = { $path }, band { $band }
eq-band-filter = cutoff { $freq_hz } Hz ({ $type })
eq-band-flat = off (0 dB)
eq-band-peq = { $type } { $freq_hz } Hz, { $gain_db } dB, Q { $q }
delay-of = Delay of { $output }
fader-of = Fader of { $path }
polarity-of = Polarity of { $path }
polarity-normal = normal
polarity-inverted = inverted
value-ms = { $ms } ms
value-db = { $db } dB
effect-junction-score = Expected: the bass where the subwoofer and the main speakers meet gets fuller — { $after_db } dB instead of { $before_db } dB (higher is better).
effect-rms-deviation = Expected: the sound gets a little more even — off the target curve by { $after_db } dB instead of { $before_db } dB (lower is better). The check measurement will show the real result.
effect-max-excess = Expected: the biggest hump (where the sound is louder than the target curve) gets smaller — it sticks out by { $after_db } dB instead of { $before_db } dB. The rest of the sound barely changes. The check measurement will show the real result.
note-crossover-more-lows-to-tops = { $path }: the new cutoff lets a little more bass into the main speakers at { $at_hz } Hz ({ $old_db } → { $new_db } dB).
note-crossover-tops-on-main = The main speakers hang directly on Main LR, which also feeds the subwoofer, so the crossover cannot be set on the console. Set a { $fc_hz } Hz high-pass in the speakers' own processor, or move the main speakers to their own matrices.
note-crossover-sub-not-on-matrix = The subwoofer is not on a matrix, so the crossover cannot be set by the program. Set a { $fc_hz } Hz low-pass for the subwoofer and a { $fc_hz } Hz high-pass for the main speakers by hand.
note-alignment-delay-sub = Delay the subwoofer by { $delay_ms } ms.
note-alignment-delay-tops = Delay the main speakers (and the other speakers with the same programme) by { $delay_ms } ms.
note-alignment-polarity = Flip the subwoofer's polarity.
note-alignment-not-needed = Subwoofer alignment: no change needed (it would gain only { $gain_db } dB).
note-sub-level = Change the subwoofer fader by { $change_db } dB.
note-sub-level-above-zero = The fader would end at { $after_db } dB, above 0 dB: turn up the subwoofer's amplifier or processor instead.
note-system-eq-on = The EQ goes on { $path }.
note-system-eq-above-fc = The subwoofer has its own path, so this EQ stays above { $fc_hz } Hz.
note-system-eq-blind-junction = From { $low_hz } to { $high_hz } Hz, where the subwoofer hands over to the main speakers, this EQ only turns the sound down a little, over a wide range: a dip there may be the two cancelling each other, and EQ cannot fix that.
note-system-eq-predicted-junction = It is designed for how the system will sound after the packages before it.
note-junction-not-measured = The subwoofer and the main speakers were not measured apart, so a crossover, alignment and subwoofer level are not proposed this time: a crossover set blind can leave a hole in the bass. They are proposed after the step “Subwoofer and main speakers apart”.
audit-what = Advice on the input channels (nothing will be changed; decide by ear, one channel at a time):
audit-by-ear = Change one setting at a time during a rehearsal and listen.
audit-voice-no-hpf = { $count ->
    [one] Channel { $channels } is a voice without a low cut (HPF): switch it on at about 100 Hz.
   *[other] Channels { $channels } are voices without a low cut (HPF): switch it on at about 100 Hz.
}
audit-voice-hpf-high = { $count ->
    [one] Voice channel { $channels } cuts the lows very high ({ $freq_hz } Hz). If the voice sounds thin, lower it 20–30 Hz at a time; for speech not below about 120 Hz.
   *[other] Voice channels { $channels } cut the lows very high (up to { $freq_hz } Hz). If a voice sounds thin, lower it 20–30 Hz at a time; for speech not below about 120 Hz.
}
audit-shared-cut = Channels { $channels } all cut around { $freq_hz } Hz (about { $gain_db } dB). A common habit; check by ear whether they are still needed, 3 dB at a time.
audit-deep-cut = { $count ->
    [one] Channel { $channels } has a very deep cut (down to { $gain_db } dB). Check whether it is still needed, 3 dB at a time.
   *[other] Channels { $channels } have very deep cuts (down to { $gain_db } dB). Check whether they are still needed, 3 dB at a time.
}
audit-big-boost = { $count ->
    [one] Channel { $channels } has a strong boost (up to +{ $gain_db } dB). It can cause feedback.
   *[other] Channels { $channels } have strong boosts (up to +{ $gain_db } dB). They can cause feedback.
}
audit-phantom-on-dynamic = { $count ->
    [one] Channel { $channels } looks like a dynamic microphone with +48V on: it is not needed there. Switch it off only while the channel is muted, or it clicks.
   *[other] Channels { $channels } look like dynamic microphones with +48V on: it is not needed there. Switch it off only while the channel is muted, or it clicks.
}
effect-sub-louder = Now the subwoofer is { $db } dB louder than the main speakers where they meet; afterwards about equal.
effect-sub-quieter = Now the subwoofer is { $db } dB quieter than the main speakers where they meet; afterwards about equal.
note-crossover-filters = Cutoff at { $fc_hz } Hz ({ $slope }) for the subwoofer and the main speakers.
note-crossover-reset = { $count ->
    [one] { $path }: { $count } old correction goes back to 0 dB (the crossover does its job now).
   *[other] { $path }: { $count } old corrections go back to 0 dB (the crossover does their job now).
}
note-crossover-shared-path = One path feeds both the subwoofer and other speakers, so a crossover cannot be set on it. Give the subwoofer its own matrix, or set a { $fc_hz } Hz cutoff in the speakers' processors.
note-crossover-check-speakers = Check that the main speakers have no low cut or “with subwoofer” mode of their own and the subwoofer no own high cut: filters in both places add up and move the crossover.
note-alignment-delay-by-hand = Some of these speakers are not on the console's own outputs: set the { $delay_ms } ms delay in their processor.
note-sub-level-by-ear = After the check measurement, adjust the subwoofer by ear for the music you play.
note-system-eq-also-other-outputs = Main LR also feeds other outputs (stream, recording, another room): they get this EQ too.
point-where = { $area }, about { $from_stage_m } m from the stage, { $from_centre_m } m { $side }
point-left-of-aisle = left of the centre aisle
point-right-of-aisle = right of the centre aisle
step-points-map-round = On the plan only this round's places are numbered.
step-points-template = The hall size was not given: this plan is for a typical 12 × 18 m hall, place the microphones roughly.
audit-possible-feedback-notch = { $count ->
    [one] Channel { $channels } has a narrow cut (down to { $gain_db } dB), probably protection against feedback. Make it at most half as deep, during a rehearsal, with the monitors on.
   *[other] Channels { $channels } have narrow cuts (down to { $gain_db } dB), probably protection against feedback. Make them at most half as deep, during a rehearsal, with the monitors on.
}
audit-shared-cut-room = Channels { $channels } all cut around { $freq_hz } Hz (about { $gain_db } dB), where the system EQ now cuts too. After the tuning, make these cuts shallower, 3 dB at a time, one channel after another.

## S12 Apply
step-apply-title = Apply the changes
apply-word = apply
undo-word = undo
write-what = Setting
write-after = Will be
value-console = { $value }
write-eq-on = { $path }: EQ on/off
write-fader = { $path }: fader
write-band-type = { $path }, band { $band }: type
write-band-freq = { $path }, band { $band }: frequency
write-band-gain = { $path }, band { $band }: gain
write-band-q = { $path }, band { $band }: width (Q)
apply-what = { $count ->
    [one] The program will write 1 setting to the console, in this order.
   *[other] The program will write { $count } settings to the console, in this order.
}
apply-undo-first = Before the first write, the old values are saved, so everything can be put back. The order is chosen so the sound never gets louder in between.
apply-type = To write, type the word “{ $word }” and press Enter. Anything else cancels.
apply-cancelled = Cancelled: nothing was written to the console.
apply-scene-not-saved = The scene on the console is NOT saved yet. Save it under a new name once you like the sound.
apply-offer-undo = Put back what was written?
apply-nothing = Nothing to write: no chosen change needs the console.
apply-proposal-unreadable = The saved change plan cannot be read. Nothing was written. Measure again to make a new plan.
apply-advice-delay = { $output }: delay { $ms } ms
apply-advice-polarity = { $path }: flip the polarity
undo-what = { $count ->
    [one] The program will put back 1 setting.
   *[other] The program will put back { $count } settings.
}
undo-type = To put them back, type the word “{ $word }” and press Enter. Anything else cancels.
undo-nothing = Nothing to put back: the console already has the old values.
undo-refused = The saved old values cannot be used. Nothing was written. Put the settings back by hand.
undo-cancelled = Cancelled: nothing was put back.
undo-done = The old values are back on the console.
undo-incomplete = Not everything could be put back. Look at the console and set the rest by hand.

## S13 Verify
verify-nothing-applied = Nothing was written to the console in this session, so there is nothing to check.
verify-better = It got better.
verify-offer-undo = Put the old values back now?
undo-console-changed = The console changed while the list was on the screen. Nothing was put back. Look at the console and start the undo again.
apply-run-refused = The safety check stopped the writing half-way ({ $count } settings had been written). Nothing more was sent.

## S12-S14 after the UX review
write-on = { $path }: sound (off = muted)
value-on = on
value-off = off
value-hz = { $hz } Hz
value-khz = { $khz } kHz
value-plain = { $value }
value-fader-down = fader down
apply-word-mismatch = That is not the word “{ $word }”, so nothing was written. Type it again, or press Enter on an empty line to cancel.
undo-word-mismatch = That is not the word “{ $word }”, so nothing was put back. Type it again, or press Enter on an empty line to cancel.
apply-console-changed-now = The console changed while the list was on the screen. Nothing was written. Here is the new list, read from the console just now.
apply-console-keeps-changing = The console keeps changing while the list is on the screen. Nothing was written. Make sure nobody is using the console, then start again.
apply-offer-check = The last setting may have been written halfway. Check it and put back the old values?
apply-console-changed = The console changed after the measurement: { $settings }. Nothing was written. Measure again so the plan fits the console: start the wizard again.
apply-refused = The program refused this plan because it breaks a safety limit. Nothing was written. The console is unchanged; you can finish here or measure again.
apply-readback-failed = The console did not confirm “{ $setting }”, so the program stopped. Settings written before: { $count }. Usually this is a network hiccup: check the cable, then put back what was written.
apply-advice-intro = Now set these by hand on the console (the program does not write them), then press Enter:
apply-stopped = You stopped writing to the console. Settings written before that: { $count }.
apply-done = { $count ->
    [one] Done: 1 setting was written and checked on the console.
   *[other] Done: { $count } settings were written and checked on the console.
}
apply-console-lost = The connection to the console was lost. Check the network cable and look at the console. If something was written, the old values are saved: the last screen shows the command that puts them back.
undo-changed-by-hand = { $count ->
    [one] 1 setting was changed by hand since; the program leaves it as it is.
   *[other] { $count } settings were changed by hand since; the program leaves them as they are.
}
step-verify-title = Measure again to check
verify-what = { $count ->
    [one] Now we measure 1 place again, to see whether the changes helped.
   *[other] Now we measure { $count } places again, to see whether the changes helped.
}
verify-which = Among them: the seat that sounded worst and the one in the middle of the hall.
verify-same-places = { $count ->
    [one] Put the microphone exactly where it stood before: same seat, same height.
   *[other] Put the microphones exactly where they stood before: same seats, same height.
}
verify-no-points = The check measurement has no place to compare with the first one. Nothing was judged.
verify-no-baseline = There is no first measurement to compare with. Nothing was judged.
verify-place = place { $number } ({ $place })
verify-m = Average distance from the target curve (how the sound should be): { $before_db } dB before, { $after_db } dB now; smaller is better.
verify-worst = The seat that sounded worst, { $place }: { $direction ->
    [better] better by { $delta_db } dB.
    [worse] worse by { $delta_db } dB.
   *[same] no change.
}
verify-band = Largest change for the worse: { $delta_db } dB at the frequency { $freq_hz } Hz, { $place }.
verify-worse = It got worse. Putting the old values back is recommended.
verify-no-change = No noticeable difference. You may keep the changes and listen at the next service; the last screen shows how to put the old values back.
verify-chart = Picture: row 1 is before, row 2 is now; the taller the block, the further from the target (63 Hz on the left, 8 kHz on the right).
step-finish-scene = After you restore the mutes and sends, save the scene on the console under a new name (for example today's date): then this tuning is easy to get back even if someone loads another scene.
step-finish-undo-later = If the sound is not right at the service, you can put the old values back: open Terminal and type { $command }
step-finish-reverted = The console has its old settings again; there is nothing to save.

## S10b Junction
mute-word = mute

## S10b Junction (after the UX review)
step-junction-title = Subwoofer and main speakers apart
mute-word-mismatch = That is not the word “{ $word }”, so nothing was switched off. Type it again, or press Enter on an empty line to skip this measurement.
junction-what = { $count ->
    [one] At 1 place we play three short test sounds: the subwoofer alone, the main speakers alone, then everything. This shows where they meet and whether they cancel each other out.
   *[other] At { $count } places we play three short test sounds: the subwoofer alone, the main speakers alone, then everything. This shows where they meet and whether they cancel each other out.
}
junction-mutes = To hear them apart, the program switches off the sound of the subwoofer or the main speakers on the console for a few seconds (the Mute button of their matrices, the console outputs that feed these speakers). Before that it writes down how every button was, and afterwards it switches them back and checks. If the console does not confirm, the program tells you which buttons to look at.
junction-type = To allow this, type the word “{ $word }” and press Enter (a typed word protects against an accidental key press). Enter on an empty line skips it: the rest of the tuning continues, only no crossover (the split between the subwoofer and the main speakers) is proposed.
junction-cancelled = Skipped: nothing was switched off. The rest of the tuning continues; no crossover is proposed this time.
junction-sweeps = three short test sounds in a row: first only the subwoofer, then only the main speakers, then everything. The silence in between is planned. Do not press Mute on the console meanwhile.
junction-done = Done: places measured with the subwoofer and the main speakers apart — { $count }. Every Mute button is back as it was.
junction-partial = { $done } of { $total } places measured. The rest were not measured, so no crossover is proposed for now.
junction-not-possible = The subwoofer and the main speakers cannot be measured apart here: the program switches off only matrices, and at least one of them does not come from its own matrix. The rest of the tuning continues. For a crossover, the subwoofer and the main speakers need their own matrices: ask whoever set up the system.
junction-no-writes = This run does not change the console, so the subwoofer and the main speakers are not measured apart. The rest of the tuning continues.
junction-skipped-pending = So this measurement is skipped, and no crossover is proposed this time.
junction-matrix = { $matrix } ({ $name })
junction-restore-by-hand = The console did not confirm that the sound is back on. Look at the console now: on { $matrices } the Mute button must not be lit. If it is lit, press it. The program checks them again before it finishes.
junction-mute-failed = The console did not confirm that the sound was switched off, so the measurement stopped. The sound is back on.
mutes-pending-what = An earlier measurement stopped before it could switch the sound back on. These speakers are silent until then (read from the console just now):
mutes-pending-type = To switch them back on, type the word “{ $word }” and press Enter. Enter on an empty line leaves them off: then these speakers stay silent.
mutes-pending-left = Left off. Take the Mute off these matrices by hand, or start the program again.
mutes-pending-changed = The console changed while the list was on the screen, so nothing was written. Start the program again to see a fresh list.
mutes-pending-done = The sound is back on: every Mute button is as it was before the measurement.
mutes-pending-cannot-finish = The program closes now: some matrices are still switched off from the measurement. Take the Mute off them by hand, or start the program again and it will offer to do it. Save the scene only after that.
apply-level-far = { $path }: the fader would go from { $first_db } dB (as it was at the first visit) to { $after_db } dB, that is { $delta_db } dB { $louder ->
    [yes] louder
   *[no] quieter
}. That is far from how the system was set up.
apply-level-far-question = Write this level anyway? “No” writes nothing at all; to keep the other changes, go back and leave this package out.
apply-advice-side-delay = { $side }: add { $ms } ms of delay (in the speakers' processor, or on the console output that feeds them)
apply-advice-side-sub = Subwoofer
apply-advice-side-tops = Main speakers
junction-rejected-clipped = At place { $number } ({ $place }) the recording was too loud (clipping), so this place is not used. Lower the level and measure it again later.
junction-rejected-quiet = At place { $number } ({ $place }) one of the three sounds was missing, so this place is not used. Check that the subwoofer and the main speakers both play.
junction-rejected-mismatch = At place { $number } ({ $place }) the three sounds do not add up: probably a Mute switched late or something moved. This place is not used.
apply-still-muted = The console did not confirm that { $matrices } came back on after a short mute. Take the Mute off them by hand now; the program checks again before it finishes.
apply-mutes-pending = Some speakers may still be muted from an earlier measurement, so nothing is written now.
verify-mutes-pending = Some speakers may still be muted, so the check measurement is skipped: it would compare the wrong sound.
apply-advice-package = { $package }: set the values of this package by hand, as shown in the change plan
