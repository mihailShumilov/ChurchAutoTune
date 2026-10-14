# 0006 — Console driver of M1: emulator package, discovery limits, snapshot files

Status: accepted (2026-10-14)

## Context

M1 adds discovery, a full read-only snapshot, topology inference and the `doctor`, `snapshot` and
`emulator` commands (SPEC §15). Three choices needed a record.

1. `ctune emulator` and `ctune --emulator` need the X32 emulator inside the installed package, but
   the static safety tests (`tests/unit/safety/test_static_guarantees.py`) allow the `socket` module
   in `churchtune/mixers/x32/transport.py` only. Those tests must never be weakened.
2. SPEC S2 asks for a broadcast of `/xinfo` on every IPv4 interface. The standard library has no
   portable way to list interface addresses, and `subprocess` (to run `ifconfig`) is one of the
   network modules the same static tests forbid outside the transport.
3. A snapshot must be stored before anything else happens and must stay readable by later versions.

## Decision

1. The emulator lives in a separate top-level package, `churchtune_emulator`, shipped in the same
   wheel. It binds to 127.0.0.1 only, imports from `churchtune` (codec, params, snapshot parsing),
   and nothing in `churchtune` imports it except the two development entry points of the CLI,
   lazily. `tests/emulator/x32_emulator.py` is now a thin subclass of it. The packaged emulator is
   seeded from the anonymized reference dump (`churchtune_emulator/data/`), and can take a model
   string, a dump or raw lines (topology fixtures), a reply latency and a seeded drop rate.
2. Discovery is a function of the transport. It sends one fixed packet, `/xinfo` without arguments,
   to the limited broadcast and to the /24 broadcast of the interface that holds the default route
   (found with a UDP `connect()`, which sends nothing); the last known address and manual entry are
   the fallback. In dev-safe mode it sends to loopback only and never broadcasts. A VPN check by
   interface is postponed: the existing "No route to host" hint covers the common case.
3. `console/<ts>-snapshot.json` (`SnapshotFile`, schema version 1) stores the identity and the raw
   reply lines only. The parsed `ConsoleSnapshot` is rebuilt by the driver, so a newer parser reads
   an old file better than the old one did. M3 moves the directory under `venues/<slug>/`.

Topology fixtures are TOML files (base dump + line overrides + expected roles) instead of YAML, so no
new dependency is needed.

## Consequences

- The socket rule for `churchtune` stays exactly as it was; the emulator's server socket is outside
  the package the rule protects. A reviewer must keep `churchtune_emulator` loopback-only.
- Consoles on networks wider than /24 that do not answer the limited broadcast need `--console`.
- Snapshot files are larger than a parsed model would be (about 2 100 lines), which is acceptable.
