## Shared words, keys and checks

common-yes = Yes
common-no = No
common-retry = Try again
common-microphone = Microphone
common-step = Step { $number } of { $total }

keys-info = [Enter] continue · [B] back · [Q] save and quit
keys-confirm = [Enter] yes · type 2 or “no” for no · [B] back · [Q] save and quit
keys-choose = Type the number, or press [Enter] for the recommended one
keys-input = Type and press [Enter]
keys-task = Do it, then press [Enter] · [Q] save and quit
keys-typed = Type the word and press [Enter]

tui-keys-info = [Enter] continue
tui-keys-choose = [↑ ↓] choose · [Enter] confirm
tui-keys-input = Type, then [Enter]
tui-keys-task = Do it, then [Enter]
tui-keys-typed = Type the word, then [Enter]
tui-keys-back = [Esc] back
tui-keys-quit = [Ctrl+Q] save and quit

check-ok = ✓ OK
check-warn = ! Check
check-fail = ✗ Problem
check-result = Result
check-what = What

sound-warning = A test signal is about to play. Nobody with sensitive hearing should stand near the speakers. Take headphones off.
sound-stop-keys = Ctrl+C or Space stops the sound at once.
sound-stop-ctrl-c = Ctrl+C stops the sound at once.

gate-clipping = The microphone was too loud and clipped: turn its gain down or play quieter.
gate-hot = Very close to clipping: turn the gain down a little.
gate-quiet = Very quiet: check phantom power (+48V) and the gain.
gate-low-snr = The room is too noisy for this level: ask for silence or play a bit louder.
gate-low-pnr = The result is too noisy: repeat in silence.
gate-xrun = The audio stream dropped out: close other apps and repeat.
gate-unsteady = The two sweeps differ: something moved or made noise. Repeat.

noise-hum = { $mic }: mains hum (dimmers, LED drivers). Turn the stage lights off.
noise-rumble = { $mic }: low rumble (ventilation, air conditioning). Turn it off if you can.
noise-loud = { $mic }: the room is noisy (talking, footsteps, traffic). Ask for silence.
common-recommended = (recommended)
common-default = Enter keeps: { $value }
common-save-quit = Save and quit
common-mic = Microphone { $number } (USB input { $usb })
common-mic-number = Microphone { $number }
common-dbfs = { $level } dBFS
plain-invalid-choice = Type a number from 1 to { $count }.
sound-play = Play the test sound
sound-later = Not yet
sound-playing = The test sound is playing · [Space] or [Ctrl+C] stops it
sound-playing-plain = The test sound is playing. Ctrl+C stops it.
sound-countdown = Step away from the microphones: the sound starts in 3 seconds
sound-stopped = Sound stopped.
session-saved-resume = The session is saved. To continue later: ctune resume
tui-quit-pending = Stopping. The session is saved at the next question.
audio-problem-channels = fewer than 32 channels
audio-problem-sample-rate = not running at 48 kHz
signal-off = off
signal-main-l = Main L
signal-main-r = Main R
signal-mono = Main Mono
signal-bus = bus { $number }
signal-matrix = matrix { $number }
signal-direct-channel = direct out of channel { $number }
signal-direct-aux = direct out of aux in { $number }
signal-direct-fx = direct out of effect return { $number }
signal-monitor = monitor out
signal-talkback = talkback
signal-unknown = unknown source
output-main = output { $number }
output-aux = aux out { $number }
alignment-delay = { $where }: delay { $ms } ms
alignment-polarity = { $where }: polarity inverted
signal-main-lr = Main LR
