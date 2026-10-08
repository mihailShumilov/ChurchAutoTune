"""Static guarantees about who may touch the network. Never weaken, skip or xfail these tests.

Ported from the original tool's ``test_raw_send_only_in_osc_and_writer`` and
``test_socket_only_in_osc``; the names follow the new layout (transport / client / writer).
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[3] / "src" / "churchtune"
TRANSPORT = "mixers/x32/transport.py"
CLIENT = "mixers/x32/client.py"
WRITER = "mixers/x32/writer.py"
# The raw send capability: defined in the transport, called once in the client (after the read
# allowlist) and once in the writer (after the write policy).
RAW_SEND = "send_packet"
RAW_SEND_CALLERS = {CLIENT: "X32Client.send", WRITER: "ScopedWriter._send"}
NETWORK_MODULES = {
    "socket", "_socket", "socketserver", "asyncio", "selectors", "ssl", "http", "urllib",
    "requests", "httpx", "trio", "anyio", "pythonosc", "zeroconf", "ctypes", "subprocess",
}  # fmt: skip
SOCKET_ATTRIBUTES = {"socket", "sendto", "sendmsg", "recvfrom", "_sock"}
DYNAMIC = {"__import__", "exec", "eval", "getattr", "setattr"}


def _sources(src):
    for path in sorted(src.rglob("*.py")):
        yield path.relative_to(src).as_posix(), path.read_text(encoding="utf-8")


def _imported_names(tree):
    """Top-level modules imported, plus every name pulled in by ``from x import name``."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
            found.update(alias.name for alias in node.names)
    return found


def _enclosing_functions(tree):
    """Map every node to the qualified name of the function that contains it."""
    owner = {}

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            name = prefix
            if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                name = f"{prefix}.{child.name}" if prefix else child.name
            owner[child] = name
            visit(child, name)

    visit(tree, "")
    return owner


def network_violations(src):
    """Every place under ``src`` that reaches for the network outside the allowed points."""
    problems = []
    for rel, source in _sources(src):
        tree = ast.parse(source)
        owner = _enclosing_functions(tree)
        if rel != TRANSPORT:
            problems += [
                f"{rel}: imports {name}" for name in sorted(_imported_names(tree) & NETWORK_MODULES)
            ]
        for node in ast.walk(tree):
            if rel != TRANSPORT and isinstance(node, ast.Name) and node.id == "socket":
                problems.append(f"{rel}: uses the name socket")
            if (
                rel != TRANSPORT
                and isinstance(node, ast.Attribute)
                and node.attr in SOCKET_ATTRIBUTES
            ):
                problems.append(f"{rel}: touches .{node.attr}")
            if isinstance(node, ast.Call):
                func = node.func
                called = (
                    func.id
                    if isinstance(func, ast.Name)
                    else func.attr
                    if isinstance(func, ast.Attribute)
                    else ""
                )
                if called in DYNAMIC:
                    problems.append(f"{rel}: calls {called}()")
                if called == RAW_SEND and RAW_SEND_CALLERS.get(rel) != owner[node]:
                    problems.append(f"{rel}: calls {RAW_SEND}() in {owner[node] or 'module'}")
        if "importlib" in _imported_names(tree):
            problems.append(f"{rel}: imports importlib")
    return problems


def _raw_send_call_sites(src):
    sites = []
    for rel, source in _sources(src):
        tree = ast.parse(source)
        owner = _enclosing_functions(tree)
        sites += [
            (rel, owner[node])
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == RAW_SEND
        ]
    return sites


def test_no_network_access_outside_the_transport_client_and_writer():
    assert network_violations(SRC) == []


def test_transport_is_the_module_that_imports_socket():
    tree = ast.parse((SRC / TRANSPORT).read_text(encoding="utf-8"))

    assert "socket" in _imported_names(tree)


def test_raw_send_is_called_exactly_once_in_the_client_after_the_allowlist():
    assert _raw_send_call_sites(SRC).count((CLIENT, "X32Client.send")) == 1


def test_raw_send_call_sites_are_exactly_the_pinned_ones():
    sites = _raw_send_call_sites(SRC)

    assert len(sites) == len(set(sites))
    assert set(sites) <= set(RAW_SEND_CALLERS.items())


def test_transport_attribute_of_the_client_is_private_to_the_client():
    users = {rel for rel, source in _sources(SRC) if "._transport" in source}

    assert users <= {CLIENT}


# ------------------------------------------------------------ the checker itself
@pytest.mark.parametrize(
    ("rel", "source", "expected"),
    [
        ("measure/point.py", "import socket\n", "imports socket"),
        ("measure/point.py", "def f():\n    import socket\n", "imports socket"),
        ("cli/main.py", "from churchtune.mixers.x32.transport import socket\n", "imports socket"),
        ("cli/main.py", "from churchtune.mixers.x32 import transport\ntransport.socket\n", ".socket"),
        ("cli/main.py", "def f(t):\n    t._sock.sendto(b'', ('h', 1))\n", ".sendto"),
        ("cli/main.py", "def f(t):\n    return t._sock\n", "._sock"),
        ("cli/main.py", "import asyncio\n", "imports asyncio"),
        ("cli/main.py", "import subprocess\n", "imports subprocess"),
        ("cli/main.py", "import importlib\n", "imports importlib"),
        ("cli/main.py", "def f(t):\n    t.send_packet(b'')\n", "calls send_packet() in f"),
        (CLIENT, "class X32Client:\n    def ask(self):\n        self.t.send_packet(b'')\n", "ask"),
        (WRITER, "class ScopedWriter:\n    def apply(self):\n        self.s.send_packet(b'')\n",
         "apply"),
        ("cli/main.py", "def f(t):\n    getattr(t, 'send_' + 'packet')(b'')\n", "calls getattr()"),
        ("cli/main.py", "__import__('socket')\n", "calls __import__()"),
        ("cli/main.py", "exec('import socket')\n", "calls exec()"),
    ],
)  # fmt: skip
def test_checker_reports_network_access(tmp_path, rel, source, expected):
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")

    problems = network_violations(tmp_path)

    assert problems
    assert any(expected in problem for problem in problems)


@pytest.mark.parametrize(
    ("rel", "source"),
    [
        (TRANSPORT, "import socket\nclass T:\n    def send_packet(self, p):\n        self._sock.sendto(p, 1)\n"),
        (CLIENT, "class X32Client:\n    def send(self):\n        self._transport.send_packet(b'')\n"),
        (WRITER, "class ScopedWriter:\n    def _send(self):\n        self._sender.send_packet(b'')\n"),
        ("measure/point.py", "import numpy\n"),
    ],
)  # fmt: skip
def test_checker_accepts_the_allowed_points(tmp_path, rel, source):
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")

    assert network_violations(tmp_path) == []
