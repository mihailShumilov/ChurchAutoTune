import pytest

from churchtune.errors import ChurchTuneError, DevSafeViolation
from churchtune.platform.devsafe import ensure_loopback_host, is_dev_safe, is_loopback_host

LAN_HOST = "192.168.0.80"


@pytest.mark.parametrize(
    "environ",
    [
        {"CTUNE_DEV_SAFE": "1"},
        {"CLAUDECODE": "1"},
        {"CLAUDECODE": ""},
        {"CLAUDECODE": "0"},
        {"CTUNE_DEV_SAFE": "0", "CLAUDECODE": "1"},
        {"CTUNE_DEV_SAFE": "", "CLAUDECODE": "1"},
        {"CTUNE_DEV_SAFE": "1", "CLAUDECODE": "1"},
        {"CTUNE_DEV_SAFE": " 1 "},
        {"CTUNE_DEV_SAFE": "true"},
        {"CTUNE_DEV_SAFE": "yes"},
        {"CTUNE_DEV_SAFE": "off"},
        {"CTUNE_DEV_SAFE": "false"},
        {"CTUNE_DEV_SAFE": "00"},
        {"CTUNE_DEV_SAFE": "-0"},
        {"CTUNE_DEV_SAFE": "0.0"},
        # Whitespace variants of "0" do not switch the mode off.
        {"CTUNE_DEV_SAFE": " 0"},
        {"CTUNE_DEV_SAFE": "0 "},
        {"CTUNE_DEV_SAFE": "0\n"},
        {"CTUNE_DEV_SAFE": " "},
    ],
)
def test_is_dev_safe_on(environ):
    assert is_dev_safe(environ) is True


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {"CTUNE_DEV_SAFE": "0"},
        {"CTUNE_DEV_SAFE": ""},
        {"PATH": "/usr/bin", "CTUNE_HOME": "/data/ctune"},
    ],
)
def test_is_dev_safe_off(environ):
    assert is_dev_safe(environ) is False


def test_is_dev_safe_reads_process_environment_by_default(monkeypatch):
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setenv("CTUNE_DEV_SAFE", "0")
    assert is_dev_safe() is False

    monkeypatch.setenv("CTUNE_DEV_SAFE", "1")
    assert is_dev_safe() is True


def test_agent_variable_in_process_environment_overrides_switch_off(monkeypatch):
    monkeypatch.setenv("CTUNE_DEV_SAFE", "0")
    monkeypatch.setenv("CLAUDECODE", "1")

    assert is_dev_safe() is True


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "127.0.0.2", "127.255.255.254", "127.0.0.0", "localhost", "::1"]
)
def test_is_loopback_host_accepts_loopback(host):
    assert is_loopback_host(host) is True


@pytest.mark.parametrize(
    "host",
    [
        LAN_HOST,
        "10.0.0.5",
        "172.16.4.1",
        "169.254.1.1",
        "8.8.8.8",
        "0.0.0.0",
        "255.255.255.255",
        "128.0.0.1",
        "126.255.255.255",
        # Host names are never resolved.
        "x32.local",
        "console",
        "localhost.evil.example",
        "LOCALHOST.",
        "Localhost",
        "localhost\n",
        "127.0.0.1.example.com",
        # Not a canonical IPv4 literal.
        "127.0.0.1 ",
        " 127.0.0.1",
        "127.0.0.1\n",
        "127.1",
        "127.0.0.01",
        "127.0.0.1/32",
        "2130706433",
        "0x7f000001",
        "127.0.0.1:10023",
        # IPv6: only the exact string "::1".
        "::1%en0",
        "::1%lo0",
        "::1%25en0",
        "[::1]",
        "0:0:0:0:0:0:0:1",
        "::0001",
        "::ffff:127.0.0.1",
        "::ffff:7f00:1",
        "::ffff:7f00:1%en0",
        "::ffff:192.168.0.80",
        "fe80::1",
        "::",
        "",
        # Not strings at all.
        2130706433,
        b"\x7f\x00\x00\x01",
        b"\x00" * 15 + b"\x01",
        None,
    ],
)
def test_is_loopback_host_rejects_everything_else(host):
    assert is_loopback_host(host) is False


@pytest.mark.parametrize(
    "environ",
    [
        {"CTUNE_DEV_SAFE": "1"},
        {"CLAUDECODE": "1"},
        {"CTUNE_DEV_SAFE": "0", "CLAUDECODE": "1"},
    ],
)
@pytest.mark.parametrize("host", [LAN_HOST, "x32.local", "127.0.0.1:10023", "::1%en0", ""])
def test_ensure_loopback_host_refuses_in_dev_safe(environ, host):
    with pytest.raises(DevSafeViolation) as excinfo:
        ensure_loopback_host(host, environ)

    assert excinfo.value.context == {"host": host}


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("127.0.0.2", "127.0.0.2"),
        ("::1", "::1"),
        # The name is never given to the resolver: the caller connects to the literal address.
        ("localhost", "127.0.0.1"),
    ],
)
def test_ensure_loopback_host_returns_address_to_connect_to_in_dev_safe(host, expected):
    assert ensure_loopback_host(host, {"CTUNE_DEV_SAFE": "1"}) == expected


@pytest.mark.parametrize("host", [LAN_HOST, "x32.local", "localhost"])
def test_ensure_loopback_host_returns_host_unchanged_outside_dev_safe(host):
    assert ensure_loopback_host(host, {}) == host


def test_ensure_loopback_host_uses_process_environment_by_default(monkeypatch):
    monkeypatch.setenv("CTUNE_DEV_SAFE", "1")

    with pytest.raises(DevSafeViolation):
        ensure_loopback_host(LAN_HOST)


def test_dev_safe_violation_is_a_churchtune_error_with_i18n_key():
    error = DevSafeViolation("refused", host="x")

    assert isinstance(error, ChurchTuneError)
    assert error.i18n_key == "err-dev-safe-violation"
    assert str(error) == "refused"
