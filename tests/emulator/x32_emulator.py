"""UDP emulator of a Behringer X32 (fw 4.09) for tests. Loopback only.

* ``/xinfo``, ``/info``, ``/status``.
* ``/node ,s <path>`` -> reply with address ``node`` (NO leading slash, like the real console),
  one ``,s`` string ``/path values...\\n``. State is seeded from a real dump; unknown nodes get a
  generic plausible line so long read-only sweeps never time out.
* plain queries of Main LR EQ, matrix EQ / mute / fader / polarity and output delays -> same
  address with ``,f`` / ``,i``.
* writes (any message with arguments that is not ``/node`` or ``/meters``) are LOGGED in
  ``writes``; writes to known parameters are quantized like the console does and applied.
* ``/meters ,s /meters/0`` -> blob (LE int32 count + LE float32 levels) every 50 ms for 10 s.
"""

import contextlib
import re
import socket
import struct
import threading
import time
from pathlib import Path

from churchtune.domain.eq import Band, SystemEq
from churchtune.mixers.x32 import params as P  # noqa: N812 - short alias kept from the original
from churchtune.mixers.x32.codec import decode, encode
from churchtune.mixers.x32.nodes import parse_band_line, parse_eq_node_line, parse_eq_on_line
from tests.fixtures.paths import DUMP_2026_09_29

LOOPBACK = "127.0.0.1"
EQ_PARAM_RE = re.compile(r"^/main/st/eq/([1-6])/(type|f|g|q)$")
PREFS_LINE = (
    '/-prefs "ChurchTune" 70 50 100 50 ON 48K INT ON ON ON ON OFF OFF ON %0000 OFF '
    'SCENES 24h OFF OFF NORM "X32P-00-00-00" USB 0 "4.09"'
)
STEPS = {
    "f": P.FREQ_STEPS,
    "g": P.GAIN_STEPS,
    "q": P.Q_STEPS,
    "fader": P.FADER_STEPS,
    "time": P.DELAY_STEPS,
}
FADER_DOWN_DB = -999.0
METER_COUNT = 70
MIC_METER_INDEX = 9  # ch10
PLAYBACK_METER_INDICES = (22, 23)  # ch23/24


def _band_x(band: Band) -> dict:
    """Normalized, quantized values of a band, as the console stores them."""
    return {
        "type": P.EQ_TYPES.index(band.type),
        "f": P.quantize_freq(P.freq_to_norm(band.freq_hz)),
        "g": P.quantize_gain(P.gain_to_norm(band.gain_db)),
        "q": P.quantize_q(P.q_to_norm(band.q)),
    }


def _band_text(x: dict) -> str:
    return (
        f"{P.EQ_TYPES[x['type']]} {P.fmt_freq(P.norm_to_freq(x['f']))} "
        f"{P.fmt_gain(P.norm_to_gain(x['g']))} {P.fmt_q(P.norm_to_q(x['q']))}"
    )


class X32Emulator:
    def __init__(self, dump_file=DUMP_2026_09_29, mic_level=0.01):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((LOOPBACK, 0))
        self.sock.settimeout(0.05)
        self.port = self.sock.getsockname()[1]
        self.nodes = {}
        self.writes = []  # (address, args) of every write attempt
        self.queries = []
        self.unknown = []  # no-arg messages and /node arguments the emulator does not know
        self.mic_level = mic_level
        self.meter_subs = {}  # peer -> expiry
        self.drop_next = 0  # simulate UDP loss
        self.ignored = set()  # addresses whose writes are logged but change nothing
        for line in Path(dump_file).read_text(encoding="utf-8").splitlines():
            if line.startswith("/"):
                self.nodes[line.split(" ", 1)[0].lstrip("/")] = line
        self.nodes.setdefault("-prefs", PREFS_LINE)
        self.eq_on = parse_eq_on_line(self.nodes["main/st/eq"])
        # store normalized indices like the console does
        self.eq_x = {
            i: _band_x(parse_eq_node_line(self.nodes[f"main/st/eq/{i}"])) for i in range(1, 7)
        }
        # matrices & output delays (sub/top alignment)
        self.mtx = {}
        for n in range(1, 7):
            bands = {
                b: _band_x(parse_band_line(self.nodes[f"mtx/{n:02d}/eq/{b}"])[1])
                for b in range(1, 7)
            }
            mix = self.nodes[f"mtx/{n:02d}/mix"].split()
            fader_db = FADER_DOWN_DB if mix[2] == "-oo" else float(mix[2])
            self.mtx[n] = {
                "bands": bands,
                "on": int(mix[1] == "ON"),
                "fader": P.quantize_fader(P.fader_to_norm(fader_db)),
                "invert": 0,
            }
        self.out_delay = {o: {"on": 0, "time": 0.0} for o in range(1, 17)}
        self._stop = threading.Event()
        self.th = threading.Thread(target=self._run, daemon=True)

    # --- state helpers
    def main_eq(self) -> SystemEq:
        bands = []
        for i in range(1, 7):
            x = self.eq_x[i]
            bands.append(
                Band(
                    i,
                    P.EQ_TYPES[x["type"]],
                    P.norm_to_freq(x["f"]),
                    P.norm_to_gain(x["g"]),
                    P.norm_to_q(x["q"]),
                )
            )
        return SystemEq(self.eq_on, tuple(bands))

    def foreign_writes(self, allowed_prefixes=("/main/st/eq/",)):
        return [w for w in self.writes if not w[0].startswith(allowed_prefixes)]

    def eq_line(self, i):
        return f"/main/st/eq/{i} {_band_text(self.eq_x[i])}"

    def node_line(self, path):
        m = re.match(r"^main/st/eq/([1-6])$", path)
        if m:
            return self.eq_line(int(m.group(1)))
        if path == "main/st/eq":
            return f"/main/st/eq {'ON' if self.eq_on else 'OFF'}"
        m = re.match(r"^mtx/0([1-6])/(eq/([1-6])|mix|preamp)$", path)
        if m:
            mx = self.mtx[int(m.group(1))]
            if m.group(3):
                return f"/{path} {_band_text(mx['bands'][int(m.group(3))])}"
            if m.group(2) == "mix":
                level = P.fmt_fader(P.norm_to_fader(mx["fader"]))
                return f"/{path} {'ON' if mx['on'] else 'OFF'} {level}"
            return f"/{path} {'ON' if mx['invert'] else 'OFF'}"
        m = re.match(r"^outputs/main/(\d\d)/delay$", path)
        if m:
            d = self.out_delay[int(m.group(1))]
            return f"/{path} {'ON' if d['on'] else 'OFF'}   {P.norm_to_delay(d['time']):.1f}"
        if path in self.nodes:
            return self.nodes[path]
        if path.startswith("headamp/"):
            return f"/{path} +0.0 OFF"
        return f"/{path} 0"

    # --- server
    def start(self):
        self.th.start()
        return self

    def stop(self):
        self._stop.set()
        self.th.join(timeout=2)
        self.sock.close()

    def _send(self, addr, peer, *args):
        # A client may already have closed its socket; the real console would not care either.
        with contextlib.suppress(OSError):
            self.sock.sendto(encode(addr, *args), peer)

    def _meters_blob(self):
        vals = [1e-5] * METER_COUNT
        vals[MIC_METER_INDEX] = self.mic_level
        for index in PLAYBACK_METER_INDICES:
            vals[index] = 0.05
        return struct.pack("<i", len(vals)) + struct.pack(f"<{len(vals)}f", *vals)

    def _run(self):
        last_meter = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now - last_meter > 0.05:
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
            addr, args = decode(data)
            if addr is None:
                continue
            self._handle(addr, args, peer)

    def _handle(self, addr, args, peer):
        if addr == "/xinfo":
            self._send("/xinfo", peer, LOOPBACK, "X32P-EMU", "X32P", "4.09")
        elif addr == "/info":
            self._send("/info", peer, "V2.07", "osc-server", "X32P", "4.09")
        elif addr == "/status":
            self._send("/status", peer, "active", LOOPBACK, "X32P-EMU")
        elif addr == "/node" and args and isinstance(args[0], str):
            path = args[0].strip().lstrip("/")
            self.queries.append(path)
            if len(args) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*", path):
                self.unknown.append((addr, list(args)))
            self._send("node", peer, self.node_line(path) + "\n")  # NOTE: no leading slash
        elif addr == "/meters" and args and args[0] == "/meters/0":
            self.meter_subs[peer] = time.monotonic() + 10.0
        elif not args:
            self._query(addr, peer)
        else:
            self.writes.append((addr, list(args)))
            self._write(addr, args)

    def _query(self, addr, peer):
        m = EQ_PARAM_RE.match(addr)
        if m:
            i, k = int(m.group(1)), m.group(2)
            v = self.eq_x[i][k]
            self._send(addr, peer, int(v) if k == "type" else float(v))
        elif addr == "/main/st/eq/on":
            self._send(addr, peer, int(self.eq_on))
        else:
            ref = self._mtx_ref(addr)
            if ref is not None:
                d, k, is_int = ref
                self._send(addr, peer, int(d[k]) if is_int else float(d[k]))
            else:
                self.unknown.append((addr, []))

    def _mtx_ref(self, addr):
        """Address -> (dict, key, is_int) for matrix / output-delay params, else None."""
        m = re.match(r"^/mtx/0([1-6])/eq/([1-6])/(type|f|g|q)$", addr)
        if m:
            k = m.group(3)
            return self.mtx[int(m.group(1))]["bands"][int(m.group(2))], k, k == "type"
        m = re.match(r"^/mtx/0([1-6])/(mix/on|mix/fader|preamp/invert)$", addr)
        if m:
            k = m.group(2).split("/")[1]
            return self.mtx[int(m.group(1))], k, k in ("on", "invert")
        m = re.match(r"^/outputs/main/(\d\d)/delay/(on|time)$", addr)
        if m and int(m.group(1)) in self.out_delay:
            k = m.group(2)
            return self.out_delay[int(m.group(1))], k, k == "on"
        return None

    @staticmethod
    def _quantized(value, key):
        steps = STEPS[key]
        return round(min(max(float(value), 0), 1) * (steps - 1)) / (steps - 1)

    def _write(self, addr, args):
        if addr in self.ignored:
            return
        m = EQ_PARAM_RE.match(addr)
        v = args[0]
        if m:
            i, k = int(m.group(1)), m.group(2)
            self.eq_x[i][k] = int(v) if k == "type" else self._quantized(v, k)
        elif addr == "/main/st/eq/on":
            self.eq_on = bool(int(v))
        else:
            ref = self._mtx_ref(addr)
            if ref is not None:
                d, k, is_int = ref
                d[k] = int(v) if is_int else self._quantized(v, k)
        # anything else: logged only (tests assert it never happens)
