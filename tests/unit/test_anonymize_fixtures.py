from scripts.anonymize_fixtures import (
    CONSOLE_ID,
    CONSOLE_NAME,
    DOC_IP,
    RENAMES,
    anonymize_text,
)

LAN_IP = "192.168.7.21"


def test_renames_listed_config_lines_and_keeps_the_rest_of_the_line():
    text = '/ch/01/config "Somebody" 74 MG 1\n/ch/11/config "PIANO" 30 RDi 11\n'

    result = anonymize_text(text)

    assert result == f'/ch/01/config "{RENAMES["/ch/01/config"]}" 74 MG 1\n' + (
        '/ch/11/config "PIANO" 30 RDi 11\n'
    )


def test_does_not_touch_matrix_names():
    text = '/mtx/03/config "Sub buffer " 25 RD\n'

    assert anonymize_text(text) == text


def test_replaces_private_addresses_with_documentation_address():
    text = f"# /xinfo {LAN_IP} X32P 4.09\n" + '{"ip": "10.1.2.3"}\n127.0.0.1 stays\n'

    result = anonymize_text(text)

    assert LAN_IP not in result
    assert "10.1.2.3" not in result
    assert result.count(DOC_IP) == 2
    assert "127.0.0.1 stays" in result


def test_replaces_console_id_everywhere():
    text = '# /xinfo X32P-AB-12-CD X32P 4.09\n/-prefs "x" 70 "X32P-AB-12-CD" USB 0 "4.09"\n'

    result = anonymize_text(text)

    assert "AB-12-CD" not in result
    assert result.count(CONSOLE_ID) == 2


def test_replaces_console_name_in_prefs_line_only():
    text = '/-prefs "myconsole" 70 50 "X32P-AB-12-CD" USB 0 "4.09"\n/dca/1/config "Band" 43\n'

    result = anonymize_text(text)

    assert result.startswith(f'/-prefs "{CONSOLE_NAME}" 70 50 "{CONSOLE_ID}" USB 0 "4.09"\n')
    assert '/dca/1/config "Band" 43' in result


def test_numeric_values_and_line_count_are_preserved():
    text = "/main/st/eq/2 PEQ 496.6 -4.00 1.2\n/mtx/03/mix ON -37.5\n/headamp/041 +50.0 ON\n"

    assert anonymize_text(text) == text


def test_is_idempotent():
    text = f'/ch/01/config "Somebody" 74 MG 1\n# /xinfo {LAN_IP} X32P-AB-12-CD X32P 4.09\n'

    once = anonymize_text(text)

    assert anonymize_text(once) == once
