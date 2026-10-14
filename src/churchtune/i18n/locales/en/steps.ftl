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
step-results-next-milestones = Proposals for changes (crossover, subwoofer alignment, system EQ) and applying them come in the next version. Your measurement is saved for them.
finding-hump = A hump around { $freq_hz } Hz (+{ $gain_db } dB): voices may sound boomy or boxy.
finding-rt60-dry = The hall is rather dry (RT60 { $rt60_s } s): good for speech.
finding-rt60-good = The hall reverberates moderately (RT60 { $rt60_s } s): good for speech and music.
finding-rt60-boomy = The hall is reverberant (RT60 { $rt60_s } s): speech gets less clear. EQ does not fix this; curtains and absorbers do.
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
point-number = seat { $number }
step-finish-undo-by-hand = Put back what you changed by hand today (mutes, sends).
step-points-put = { $count ->
    [one] Put the microphone here:
   *[other] Put the microphones at these places:
}
