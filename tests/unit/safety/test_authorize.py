"""Authorization of write plans. Never weaken, skip or xfail these tests."""

import ast
import dataclasses
from pathlib import Path

import pytest

from churchtune.domain.changes import Change, WriteScope
from churchtune.errors import UnsafeWrite
from churchtune.mixers.x32 import params as P  # noqa: N812 - short alias kept from the original
from churchtune.safety.authorize import WriteAuthorization, authorize, is_confirmation, verify
from churchtune.safety.policy import WritePolicy
from tests.fixtures.venues import reference_policy

POLICY = reference_policy()
SRC = Path(__file__).resolve().parents[3] / "src" / "churchtune"
UNDO = Path("undo/undo_test.json")
GAIN_DOWN = Change("/main/st/eq/2/g", 0.5, P.quantize_gain(P.gain_to_norm(-3.0)), "+0.00", "-3.00")
FADER = Change("/mtx/03/mix/fader", 0.2033, P.fader_to_norm(-31.5), "-37.5", "-31.5")


@pytest.mark.parametrize("answer", ["y", "Y", "yes", "YES", " y ", "д", "да", "Да", "yes\n"])
def test_explicit_yes_confirms(answer):
    assert is_confirmation(answer) is True


@pytest.mark.parametrize(
    "answer", ["", " ", "n", "no", "нет", "ok", "1", "true", "apply", "yy", "y!", "yes please"]
)
def test_anything_else_does_not_confirm(answer):
    assert is_confirmation(answer) is False


def test_authorize_issues_an_authorization_for_a_valid_confirmed_plan():
    auth = authorize(WriteScope.MAIN, [GAIN_DOWN], UNDO, "y", POLICY)

    assert (auth.scope, auth.changes, auth.undo_path) == (WriteScope.MAIN, (GAIN_DOWN,), UNDO)
    assert auth.policy is POLICY
    assert verify(auth) is auth


@pytest.mark.parametrize("answer", ["", "n", "no", "maybe"])
def test_authorize_refuses_without_confirmation(answer):
    with pytest.raises(UnsafeWrite, match="did not confirm"):
        authorize(WriteScope.MAIN, [GAIN_DOWN], UNDO, answer, POLICY)


def test_authorize_refuses_a_plan_with_a_change_outside_the_scope():
    with pytest.raises(UnsafeWrite, match="path not allowed"):
        authorize(WriteScope.MAIN, [GAIN_DOWN, FADER], UNDO, "y", POLICY)


def test_authorize_refuses_a_change_outside_the_limits():
    too_loud = dataclasses.replace(FADER, new_value=P.fader_to_norm(6.0))

    with pytest.raises(UnsafeWrite, match="fader"):
        authorize(WriteScope.SUB, [too_loud], UNDO, "y", POLICY)


def test_authorize_checks_the_confirmation_before_anything_else():
    bad = Change("/ch/10/mix/on", 0, 1, "OFF", "ON")

    with pytest.raises(UnsafeWrite, match="did not confirm"):
        authorize(WriteScope.SUB, [bad], UNDO, "", POLICY)


def test_authorization_is_immutable_and_keeps_the_order_of_the_plan():
    auth = authorize(WriteScope.SUB, [FADER, FADER], UNDO, "да", POLICY)

    assert auth.changes == (FADER, FADER)
    with pytest.raises(dataclasses.FrozenInstanceError):
        auth.scope = WriteScope.MAIN  # type: ignore[misc]


def test_an_empty_plan_can_be_authorized_and_writes_nothing():
    assert authorize(WriteScope.MAIN, [], UNDO, "y", POLICY).changes == ()


class _PermissivePolicy(WritePolicy):
    def validate(self, path, value, scope):
        pass


def test_authorize_refuses_a_policy_subclass():
    permissive = _PermissivePolicy(POLICY.laws, POLICY.matrices)

    with pytest.raises(UnsafeWrite, match="exactly a WritePolicy"):
        authorize(
            WriteScope.SUB, [Change("/ch/10/mix/on", 0, 1, "OFF", "ON")], UNDO, "y", permissive
        )


def test_verify_rejects_an_authorization_altered_with_replace():
    auth = authorize(WriteScope.MAIN, [GAIN_DOWN], UNDO, "y", POLICY)
    unmute_mic = Change("/ch/10/mix/on", 0, 1, "OFF", "ON")

    for forged in (
        dataclasses.replace(auth, changes=(unmute_mic,)),
        dataclasses.replace(auth, scope=WriteScope.SUB),
        dataclasses.replace(auth, undo_path=Path("other.json")),
        dataclasses.replace(auth, changes=(*auth.changes, GAIN_DOWN)),
        dataclasses.replace(auth, changes=()),
        dataclasses.replace(auth, changes=(dataclasses.replace(GAIN_DOWN, new_value=0.6),)),
        dataclasses.replace(
            auth, policy=WritePolicy(POLICY.laws, POLICY.matrices, allow_unverified_writes=True)
        ),
        dataclasses.replace(auth, policy=WritePolicy(POLICY.laws, frozenset({1, 2, 3, 4, 5, 6}))),
    ):
        with pytest.raises(UnsafeWrite, match="altered"):
            verify(forged)


def test_verify_rejects_a_hand_built_authorization():
    with pytest.raises(UnsafeWrite, match="altered"):
        verify(WriteAuthorization(WriteScope.MAIN, (GAIN_DOWN,), UNDO, POLICY, "0" * 64))
    with pytest.raises(UnsafeWrite, match="altered"):
        verify(WriteAuthorization(WriteScope.MAIN, (GAIN_DOWN,), UNDO, POLICY, ""))


def test_verify_rejects_a_policy_subclass_smuggled_into_a_real_authorization():
    auth = authorize(WriteScope.MAIN, [GAIN_DOWN], UNDO, "y", POLICY)
    permissive = _PermissivePolicy(POLICY.laws, POLICY.matrices)

    with pytest.raises(UnsafeWrite, match="exactly a WritePolicy"):
        verify(dataclasses.replace(auth, policy=permissive))


def test_verify_rejects_other_objects_and_subclasses():
    class Lookalike(WriteAuthorization):
        pass

    auth = authorize(WriteScope.MAIN, [GAIN_DOWN], UNDO, "y", POLICY)
    lookalike = Lookalike(auth.scope, auth.changes, auth.undo_path, auth.policy, auth.seal)
    for not_auth in (None, "y", {"scope": "main"}, [GAIN_DOWN], lookalike):
        with pytest.raises(UnsafeWrite, match="needs a WriteAuthorization"):
            verify(not_auth)


def test_seal_distinguishes_int_from_float_values():
    as_int = authorize(
        WriteScope.SUB, [Change("/mtx/03/mix/on", 1, 0, "ON", "OFF")], UNDO, "y", POLICY
    )
    as_float = dataclasses.replace(as_int, changes=(Change("/mtx/03/mix/on", 1, 0.0, "ON", "OFF"),))

    with pytest.raises(UnsafeWrite, match="altered"):
        verify(as_float)


def test_write_authorization_is_constructed_only_inside_authorize():
    constructors = []
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name | ast.Attribute)
                and (
                    getattr(node.func, "id", None) == "WriteAuthorization"
                    or getattr(node.func, "attr", None) == "WriteAuthorization"
                )
            ):
                constructors.append(path.relative_to(SRC).as_posix())

    assert constructors == ["safety/authorize.py"]
