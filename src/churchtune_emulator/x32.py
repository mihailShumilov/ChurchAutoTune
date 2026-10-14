"""UDP emulator of a Behringer X32 / Midas M32 (fw 4.x) for development and tests. Loopback only.

It replies like the real console, quirks included:

* ``/xinfo``, ``/info``, ``/status`` with the configured model and firmware.
* ``/node ,s <path>``: the reply address is ``node`` (no leading slash), one ``,s`` string
  ``/path values...`` plus a newline. State is seeded from a real dump; unknown nodes get a
  generic line so long read-only sweeps never time out.
* Plain queries of Main LR EQ, matrix EQ / mute / fader / polarity and output delays: the same
  address with ``,f`` / ``,i``.
* Writes (any message with arguments that is not ``/node`` or ``/meters``) are logged in
  ``writes``; writes to known parameters are quantized like the console does and applied.
* ``/meters ,s /meters/0``: a blob (LE int32 count + LE float32 levels) every 50 ms for 10 s.

Fault injection: ``latency_s`` per reply, ``drop_rate`` (seeded), ``drop_next``, ``ignored``
(writes logged but not applied), ``silent`` (parameter queries without a reply).

This package holds the only server socket of the project; it binds to 127.0.0.1 and nothing else.
"""

import contextlib
import random
import re
import socket
import struct
import threading
import time
from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from typing import Any, Self

from churchtune.domain.eq import Band, SystemEq
from churchtune.mixers.x32 import params as p
from churchtune.mixers.x32.codec import OscArg, decode, encode
from churchtune.mixers.x32.nodes import parse_band_line, parse_eq_node_line, parse_eq_on_line
from churchtune.mixers.x32.snapshot import raw_from_dump

LOOPBACK = "127.0.0.1"
DEFAULT_MODEL = "X32P"
DEFAULT_FIRMWARE = "4.09"
REFERENCE_DUMP = "x32p_reference_2026-10-02.txt"
EQ_PARAM_RE = re.compile(r"^/main/st/eq/([1-6])/(type|f|g|q)$")
NODE_PATH_RE = re.compile(r"[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*")
PREFS_LINE = (
    '/-prefs "ChurchTune" 70 50 100 50 ON 48K INT ON ON ON ON OFF OFF ON %0000 OFF '
    'SCENES 24h OFF OFF NORM "{name}" USB 0 "{firmware}"'
)
STEPS = {
    "f": p.FREQ_STEPS,
    "g": p.GAIN_STEPS,
    "q": p.Q_STEPS,
    "fader": p.FADER_STEPS,
    "time": p.DELAY_STEPS,
}
FADER_DOWN_DB = -999.0
METER_COUNT = 70
MIC_METER_INDEX = 9  # ch10
PLAYBACK_METER_INDICES = (22, 23)  # ch23/24
METER_PERIOD_S = 0.05
METER_SUBSCRIPTION_S = 10.0
POLL_S = 0.05
MATRICES = range(1, 7)
BANDS = range(1, 7)
OUTPUTS = range(1, 17)

Params = dict[str, float]


def reference_dump() -> str:
    """Text of the anonymized reference dump shipped with the package."""
    return resources.files(__package__).joinpath("data", REFERENCE_DUMP).read_text("utf-8")


def _band_x(band: Band) -> Params:
    """Normalized, quantized values of a band, as the console stores them."""
    return {
        "type": float(p.EQ_TYPES.index(band.type)),
        "f": p.quantize_freq(p.freq_to_norm(band.freq_hz)),
        "g": p.quantize_gain(p.gain_to_norm(band.gain_db)),
        "q": p.quantize_q(p.q_to_norm(band.q)),
    }


def _band_text(x: Params) -> str:
    return (
        f"{p.EQ_TYPES[int(x['type'])]} {p.fmt_freq(p.norm_to_freq(x['f']))} "
        f"{p.fmt_gain(p.norm_to_gain(x['g']))} {p.fmt_q(p.norm_to_q(x['q']))}"
    )


class X32Emulator:
    """An X32 / M32 on a random loopback port, seeded from a dump."""

    def __init__(  # noqa: PLR0913 - every knob is a test or dev option
        self,
        dump_file: Path | None = None,
        *,
        raw: Mapping[str, str] | None = None,
        model: str = DEFAULT_MODEL,
        firmware: str = DEFAULT_FIRMWARE,
        mic_level: float = 0.01,
        latency_s: float = 0.0,
        drop_rate: float = 0.0,
        seed: int = 0,
        port: int = 0,
    ) -> None:
        """Bind to 127.0.0.1 and load the state.

        Args:
            dump_file: A ``/node`` dump to seed from; the packaged reference dump when omitted.
            raw: Node lines by path, used instead of a dump file (topology fixtures).
            model: Model string answered to ``/xinfo``.
            firmware: Firmware string answered to ``/xinfo``.
            mic_level: Linear meter level of channel 10.
            latency_s: Delay before every reply.
            drop_rate: Share of incoming packets silently dropped, 0..1.
            seed: Seed of the drop decisions.
            port: UDP port; a free one when 0.
        """
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((LOOPBACK, port))
        self.sock.settimeout(POLL_S)
        self.port: int = self.sock.getsockname()[1]
        self.model, self.firmware = model, firmware
        self.name = f"{model}-EMU"
        self.writes: list[tuple[str, list[OscArg]]] = []
        self.queries: list[str] = []
        self.unknown: list[tuple[str, list[OscArg]]] = []
        self.mic_level = mic_level
        self.latency_s = latency_s
        self.drop_rate = drop_rate
        self._random = random.Random(seed)  # noqa: S311 - fault injection, not security
        self.meter_subs: dict[tuple[str, int], float] = {}
        self.drop_next = 0
        self.ignored: set[str] = set()
        self.silent: set[str] = set()
        # Node path -> extra delay before its next reply (once); nothing else is answered meanwhile.
        self.slow_nodes: dict[str, float] = {}
        if raw is None:
            text = dump_file.read_text(encoding="utf-8") if dump_file else reference_dump()
            raw = raw_from_dump(text)
        self.nodes: dict[str, str] = dict(raw)
        self.nodes.setdefault("-prefs", PREFS_LINE.format(name=self.name, firmware=firmware))
        self.eq_on = parse_eq_on_line(self.nodes["main/st/eq"])
        self.eq_x = {i: _band_x(parse_eq_node_line(self.nodes[f"main/st/eq/{i}"])) for i in BANDS}
        # Per matrix: "on", "fader", "invert" and "bands" (band number -> params), the shape the
        # original tests inspect.
        self.mtx: dict[int, dict[str, Any]] = {}
        for n in MATRICES:
            bands = {
                b: _band_x(parse_band_line(self.nodes[f"mtx/{n:02d}/eq/{b}"])[1]) for b in BANDS
            }
            mix = self.nodes[f"mtx/{n:02d}/mix"].split()
            fader_db = FADER_DOWN_DB if mix[2] == "-oo" else float(mix[2])
            self.mtx[n] = {
                "on": float(mix[1] == "ON"),
                "fader": p.quantize_fader(p.fader_to_norm(fader_db)),
                "invert": 0.0,
                "bands": bands,
            }
        self.out_delay: dict[int, dict[str, float]] = {o: {"on": 0.0, "time": 0.0} for o in OUTPUTS}
        self._stop = threading.Event()
        self.th = threading.Thread(target=self._run, daemon=True)

    # ------------------------------------------------------------------ state helpers
    def main_eq(self) -> SystemEq:
        """Main LR EQ as currently stored."""
        bands = [
            Band(
                i,
                p.EQ_TYPES[int(x["type"])],
                p.norm_to_freq(x["f"]),
                p.norm_to_gain(x["g"]),
                p.norm_to_q(x["q"]),
            )
            for i, x in self.eq_x.items()
        ]
        return SystemEq(self.eq_on, tuple(bands))

    def foreign_writes(
        self, allowed_prefixes: tuple[str, ...] = ("/main/st/eq/",)
    ) -> list[tuple[str, list[OscArg]]]:
        """Writes outside the given address prefixes."""
        return [w for w in self.writes if not w[0].startswith(allowed_prefixes)]

    def press_mute(self, channel: int) -> None:
        """A person pressing MUTE on an input channel (not an OSC write; for tests and demos)."""
        path = f"ch/{channel:02d}/mix"
        head, _, rest = self.nodes[path].partition(" ")
        state, _, tail = rest.partition(" ")
        if state == "ON":
            self.nodes[path] = f"{head} OFF {tail}"

    def eq_line(self, i: int) -> str:
        """``/node`` line of Main LR band ``i``."""
        return f"/main/st/eq/{i} {_band_text(self.eq_x[i])}"

    def node_line(self, path: str) -> str:  # noqa: PLR0911 - one branch per node family
        """``/node`` reply line for ``path``."""
        if m := re.match(r"^main/st/eq/([1-6])$", path):
            return self.eq_line(int(m.group(1)))
        if path == "main/st/eq":
            return f"/main/st/eq {'ON' if self.eq_on else 'OFF'}"
        if m := re.match(r"^mtx/0([1-6])/(eq/([1-6])|mix|preamp)$", path):
            number = int(m.group(1))
            mx = self.mtx[number]
            if m.group(3):
                return f"/{path} {_band_text(self.mtx[number]['bands'][int(m.group(3))])}"
            if m.group(2) == "mix":
                level = p.fmt_fader(p.norm_to_fader(mx["fader"]))
                return f"/{path} {'ON' if mx['on'] else 'OFF'} {level}"
            return f"/{path} {'ON' if mx['invert'] else 'OFF'}"
        m = re.match(r"^outputs/main/(\d\d)/delay$", path)
        if m and int(m.group(1)) in self.out_delay:
            d = self.out_delay[int(m.group(1))]
            return f"/{path} {'ON' if d['on'] else 'OFF'}   {p.norm_to_delay(d['time']):.1f}"
        if path in self.nodes:
            return self.nodes[path]
        if path.startswith("headamp/"):
            return f"/{path} +0.0 OFF"
        return f"/{path} 0"

    # ------------------------------------------------------------------ server
    def start(self) -> Self:
        """Start serving in a daemon thread."""
        self.th.start()
        return self

    def stop(self) -> None:
        """Stop serving and close the socket."""
        self._stop.set()
        self.th.join(timeout=2)
        self.sock.close()

    def serve_forever(self) -> None:
        """Serve in the calling thread until ``stop`` or KeyboardInterrupt."""
        try:
            self._run()
        finally:
            self.sock.close()

    def _send(self, addr: str, peer: tuple[str, int], *args: OscArg) -> None:
        if self.latency_s:
            time.sleep(self.latency_s)
        # A client may already have closed its socket; the real console would not care either.
        with contextlib.suppress(OSError):
            self.sock.sendto(encode(addr, *args), peer)

    def _meters_blob(self) -> bytes:
        vals = [1e-5] * METER_COUNT
        vals[MIC_METER_INDEX] = self.mic_level
        for index in PLAYBACK_METER_INDICES:
            vals[index] = 0.05
        return struct.pack("<i", len(vals)) + struct.pack(f"<{len(vals)}f", *vals)

    def _run(self) -> None:
        last_meter = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now - last_meter > METER_PERIOD_S:
                last_meter = now
                for peer, expiry in list(self.meter_subs.items()):
                    if now > expiry:
                        del self.meter_subs[peer]
                    else:
                        self._send("/meters/0", peer, self._meters_blob())
            try:
                data, peer = self.sock.recvfrom(65536)
            except OSError:
                continue
            if self.drop_next > 0:
                self.drop_next -= 1
                continue
            if self.drop_rate and self._random.random() < self.drop_rate:
                continue
            addr, args = decode(data)
            if addr is None:
                continue
            self._handle(addr, args, peer)

    def _handle(self, addr: str, args: list[OscArg], peer: tuple[str, int]) -> None:
        if addr == "/xinfo":
            self._send("/xinfo", peer, LOOPBACK, self.name, self.model, self.firmware)
        elif addr == "/info":
            self._send("/info", peer, "V2.07", "osc-server", self.model, self.firmware)
        elif addr == "/status":
            self._send("/status", peer, "active", LOOPBACK, self.name)
        elif addr == "/node" and args and isinstance(args[0], str):
            path = args[0].strip().lstrip("/")
            self.queries.append(path)
            if len(args) != 1 or not NODE_PATH_RE.fullmatch(path):
                self.unknown.append((addr, list(args)))
            line = self.node_line(path) + "\n"  # NOTE: the reply address has no leading slash
            if path in self.slow_nodes:
                time.sleep(self.slow_nodes.pop(path))  # a busy console: later requests wait too
            self._send("node", peer, line)
        elif addr == "/meters" and args and args[0] == "/meters/0":
            self.meter_subs[peer] = time.monotonic() + METER_SUBSCRIPTION_S
        elif not args:
            self._query(addr, peer)
        else:
            self.writes.append((addr, list(args)))
            self._write(addr, args)

    def _query(self, addr: str, peer: tuple[str, int]) -> None:
        if addr in self.silent:
            return
        if m := EQ_PARAM_RE.match(addr):
            key = m.group(2)
            value = self.eq_x[int(m.group(1))][key]
            self._send(addr, peer, int(value) if key == "type" else float(value))
        elif addr == "/main/st/eq/on":
            self._send(addr, peer, int(self.eq_on))
        elif (ref := self._param_ref(addr)) is not None:
            store, key, is_int = ref
            self._send(addr, peer, int(store[key]) if is_int else float(store[key]))
        else:
            self.unknown.append((addr, []))

    def _param_ref(self, addr: str) -> tuple[dict[str, Any], str, bool] | None:
        """Address -> (store, key, is_int) for matrix and output-delay params, else None."""
        if m := re.match(r"^/mtx/0([1-6])/eq/([1-6])/(type|f|g|q)$", addr):
            key = m.group(3)
            return self.mtx[int(m.group(1))]["bands"][int(m.group(2))], key, key == "type"
        if m := re.match(r"^/mtx/0([1-6])/(mix/on|mix/fader|preamp/invert)$", addr):
            key = m.group(2).split("/")[1]
            return self.mtx[int(m.group(1))], key, key in ("on", "invert")
        m = re.match(r"^/outputs/main/(\d\d)/delay/(on|time)$", addr)
        if m and int(m.group(1)) in self.out_delay:
            key = m.group(2)
            return self.out_delay[int(m.group(1))], key, key == "on"
        return None

    @staticmethod
    def _quantized(value: OscArg, key: str) -> float:
        steps = STEPS[key]
        number = float(value) if isinstance(value, int | float) else 0.0
        return round(min(max(number, 0.0), 1.0) * (steps - 1)) / (steps - 1)

    def _write(self, addr: str, args: list[OscArg]) -> None:
        if addr in self.ignored:
            return
        value = args[0]
        if m := EQ_PARAM_RE.match(addr):
            key = m.group(2)
            band = self.eq_x[int(m.group(1))]
            band[key] = float(int(value)) if key == "type" else self._quantized(value, key)
        elif addr == "/main/st/eq/on":
            self.eq_on = bool(int(value))
        elif (ref := self._param_ref(addr)) is not None:
            store, key, is_int = ref
            store[key] = float(int(value)) if is_int else self._quantized(value, key)
        # Anything else is logged only (tests assert it never happens).
