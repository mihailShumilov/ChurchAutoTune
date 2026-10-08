from scripts.check_coverage import Gate, evaluate


def _file(covered_lines, statements, covered_branches=0, branches=0):
    return {
        "summary": {
            "covered_lines": covered_lines,
            "num_statements": statements,
            "covered_branches": covered_branches,
            "num_branches": branches,
        }
    }


GATES = (
    Gate("safety", "src/churchtune/safety/*", 100.0),
    Gate("writer", "src/churchtune/mixers/*/writer.py", 100.0),
    Gate("measure", "src/churchtune/measure/*", 90.0),
)


def test_evaluate_passes_when_every_gate_is_met():
    files = {
        "src/churchtune/safety/policy.py": _file(10, 10, 4, 4),
        "src/churchtune/mixers/x32/writer.py": _file(5, 5),
        "src/churchtune/measure/gates.py": _file(19, 20),
    }

    failures = evaluate(files, GATES)

    assert failures == []


def test_evaluate_reports_gate_below_threshold():
    files = {"src/churchtune/safety/policy.py": _file(10, 10, 3, 4)}

    failures = evaluate(files, GATES)

    assert len(failures) == 1
    assert failures[0].startswith("safety:")


def test_evaluate_counts_branches_together_with_lines():
    files = {"src/churchtune/measure/gates.py": _file(10, 10, 0, 10)}

    failures = evaluate(files, GATES)

    assert failures == ["measure: 50.0% < 90.0% (10 of 20 lines+branches missed)"]


def test_evaluate_ignores_gates_without_measured_code():
    failures = evaluate({}, GATES)

    assert failures == []


def test_evaluate_matches_writer_only_by_file_name():
    files = {"src/churchtune/mixers/x32/params.py": _file(0, 10)}

    failures = evaluate(files, GATES)

    assert failures == []
