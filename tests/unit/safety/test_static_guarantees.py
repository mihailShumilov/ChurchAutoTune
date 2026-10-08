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
            # Any mention of the raw send outside its two pinned functions, called or not
            # (``f = t.send_packet`` would otherwise slip past a check of calls only).
            if (
                rel != TRANSPORT
                and isinstance(node, ast.Attribute)
                and node.attr == RAW_SEND
                and RAW_SEND_CALLERS.get(rel) != owner[node]
            ):
                problems.append(f"{rel}: uses {RAW_SEND} in {owner[node] or 'module'}")
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


WRITER_MODULE = "churchtune.mixers.x32.writer"
WRITER_IMPORTERS = ("cli", "wizard")


def _writer_imports(tree, rel):
    """(in_function, line) of every import that brings in the writer module, in any spelling."""
    owner = _enclosing_functions(tree)
    package = ["churchtune", *rel.split("/")[:-1]]
    found = []
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - (node.level - 1)] if node.level else []
            module = ".".join([*base, *((node.module or "").split(".") if node.module else [])])
            names = [module, *(f"{module}.{alias.name}" for alias in node.names)]
        if any(name == WRITER_MODULE or name.startswith(WRITER_MODULE + ".") for name in names):
            in_function = any(
                isinstance(parent, ast.FunctionDef | ast.AsyncFunctionDef)
                for parent in _ancestors(tree, node)
            )
            found.append((in_function, owner[node], node.lineno))
    return found


def _ancestors(tree, target):
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    node = target
    while node in parents:
        node = parents[node]
        yield node


def writer_import_violations(src):
    """Imports of the writer that are not lazy, or come from a layer that must not write."""
    problems = []
    for rel, source in _sources(src):
        if rel == WRITER:
            continue
        for in_function, where, line in _writer_imports(ast.parse(source), rel):
            if rel.split("/")[0] not in WRITER_IMPORTERS:
                problems.append(f"{rel}:{line}: this layer must not import the writer")
            elif not in_function:
                problems.append(f"{rel}:{line}: writer imported outside a function ({where})")
    return problems


def test_writer_is_imported_lazily_and_only_by_the_cli_or_wizard():
    """Ported from ``test_writer_imported_only_inside_apply_and_undo``."""
    assert writer_import_violations(SRC) == []


@pytest.mark.parametrize(
    ("rel", "source"),
    [
        ("measure/point.py", "from churchtune.mixers.x32.writer import ScopedWriter\n"),
        ("measure/point.py", "import churchtune.mixers.x32.writer\n"),
        ("measure/point.py", "import churchtune.mixers.x32.writer as w\n"),
        ("measure/point.py", "from churchtune.mixers.x32 import writer\n"),
        (
            "measure/point.py",
            "def f():\n    from churchtune.mixers.x32.writer import ScopedWriter\n",
        ),
        ("mixers/x32/client.py", "from .writer import ScopedWriter\n"),
        ("mixers/x32/client.py", "from . import writer\n"),
        ("app/screens.py", "def f():\n    from churchtune.mixers.x32 import writer\n"),
        ("cli/apply.py", "from churchtune.mixers.x32.writer import ScopedWriter\n"),
        (
            "cli/apply.py",
            "try:\n    from churchtune.mixers.x32 import writer\nexcept ImportError:\n    pass\n",
        ),
        ("cli/apply.py", "if True:\n    import churchtune.mixers.x32.writer\n"),
        ("cli/apply.py", "class C:\n    from churchtune.mixers.x32.writer import ScopedWriter\n"),
    ],
)
def test_writer_import_checker_reports_forbidden_imports(tmp_path, rel, source):
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")

    assert len(writer_import_violations(tmp_path)) == 1


@pytest.mark.parametrize(
    ("rel", "source"),
    [
        (
            "cli/apply.py",
            "def apply():\n    from churchtune.mixers.x32.writer import ScopedWriter\n",
        ),
        ("wizard/steps.py", "def run():\n    import churchtune.mixers.x32.writer as w\n"),
        ("cli/apply.py", "from churchtune.safety.authorize import authorize\n"),
        ("storage/typewriter.py", "import json\n"),
        ("cli/main.py", "from churchtune.storage import typewriter\n"),
    ],
)
def test_writer_import_checker_accepts_lazy_imports_in_the_cli_and_wizard(tmp_path, rel, source):
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")

    assert writer_import_violations(tmp_path) == []


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
        ("cli/main.py", "def f(t):\n    t.send_packet(b'')\n", "uses send_packet in f"),
        ("cli/main.py", "def f(t):\n    g = t.send_packet\n    g(b'')\n", "uses send_packet in f"),
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
