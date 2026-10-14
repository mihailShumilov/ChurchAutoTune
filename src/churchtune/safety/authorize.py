"""Authorization of a write plan.

The writer accepts nothing but a ``WriteAuthorization``. One exists only after every change of
the plan passed the policy and a human confirmed. The undo file named in it must be on disk
before the first write; the writer checks that, because ``safety`` does no IO.

An authorization is sealed: ``authorize`` signs its content with a key private to this process,
and ``verify`` rejects an object that was built or altered anywhere else (for example with
``dataclasses.replace``) or that carries a policy subclass.
"""

import hashlib
import hmac
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from churchtune.domain.changes import Change, WriteScope
from churchtune.errors import UnsafeWrite
from churchtune.safety.policy import WritePolicy

# Answers that confirm a write, as typed by the operator at the prompt (expert CLI, M0).
CONFIRMATIONS: frozenset[str] = frozenset({"y", "yes", "д", "да"})
_SEAL_KEY = secrets.token_bytes(32)
MIN_WORD_LENGTH = 4
SHA256_HEX_LENGTH = 64


@dataclass(frozen=True, slots=True)
class TypedWord:
    """A confirmation word the person typed, and the word that was asked for (SPEC §9.3).

    Attributes:
        typed: What the person typed.
        expected: The word shown on screen (``apply`` in the interface language).
    """

    typed: str
    expected: str

    def matches(self) -> bool:
        """True when the typed word is the expected one (case and spaces aside)."""
        expected = self.expected.strip().casefold()
        return len(expected) >= MIN_WORD_LENGTH and self.typed.strip().casefold() == expected


def is_confirmation(answer: str) -> bool:
    """Return True when ``answer`` is an explicit yes; an empty answer is a no."""
    return answer.strip().lower() in CONFIRMATIONS


@dataclass(frozen=True, slots=True)
class WriteAuthorization:
    """Permission to send one ordered plan in one scope. Issued only by :func:`authorize`.

    Attributes:
        scope: Scope every change was validated in.
        changes: The writes, in the order they must be sent.
        undo_path: Undo file holding the old values of the whole plan.
        policy: The policy that validated the plan; the writer re-validates with it.
        seal: Signature over the other fields; see :func:`verify`.
        undo_sha256: Hash of the undo file's content; when set, the writer refuses to start
            unless the file on disk still has exactly that content.
    """

    scope: WriteScope
    changes: tuple[Change, ...]
    undo_path: Path
    policy: WritePolicy
    seal: str
    undo_sha256: str = ""


def _seal(
    scope: WriteScope,
    changes: tuple[Change, ...],
    undo_path: Path,
    policy: WritePolicy,
    undo_sha256: str = "",
) -> str:
    content = repr(
        (
            str(scope),
            undo_sha256,
            tuple((c.path, type(c.new_value).__name__, repr(c.new_value)) for c in changes),
            str(undo_path),
            type(policy.laws).__qualname__,
            tuple(sorted(policy.matrices)),
            repr(policy.limits),
            policy.allow_unverified_writes,
            tuple(sorted(policy.sub_matrices)),
            tuple(sorted(policy.top_matrices)),
            policy.mute_originals,
            policy.level_baselines,
            policy.allow_cut_filters,
        )
    )
    return hmac.new(_SEAL_KEY, content.encode(), hashlib.sha256).hexdigest()


def _issue(
    scope: WriteScope,
    changes: Sequence[Change],
    undo_path: Path,
    policy: WritePolicy,
    undo_sha256: str,
) -> WriteAuthorization:
    """Validate every change and seal the authorization (the only place one is built)."""
    plan = tuple(changes)
    for change in plan:
        policy.validate(change.path, change.new_value, scope)
    seal = _seal(scope, plan, undo_path, policy, undo_sha256)
    return WriteAuthorization(scope, plan, undo_path, policy, seal, undo_sha256)


# The scopes of the expert CLI that predate the typed word (ADR 0010); every other scope is
# authorized only by ``authorize_typed``.
YES_SCOPES = frozenset({WriteScope.MAIN, WriteScope.SUB})


def authorize(
    scope: WriteScope,
    changes: Sequence[Change],
    undo_path: Path,
    confirmation: str,
    policy: WritePolicy,
) -> WriteAuthorization:
    """Validate a plan and the operator's confirmation, and issue the authorization.

    Args:
        scope: Scope of the plan.
        changes: Ordered writes.
        undo_path: Where the undo file for the whole plan is (or is about to be) written.
        confirmation: What the operator typed at the confirmation prompt.
        policy: Write policy of the venue.

    Returns:
        The sealed authorization for the writer.

    Raises:
        UnsafeWrite: The scope needs a typed word (every scope but ``main`` and ``sub``), the
            operator did not confirm, the policy is not exactly a ``WritePolicy``, or a change
            is outside the policy.
    """
    if scope not in YES_SCOPES:
        raise UnsafeWrite(f"the {scope.value} scope needs a typed word (authorize_typed)")
    if not is_confirmation(confirmation):
        raise UnsafeWrite("the operator did not confirm the write")
    if type(policy) is not WritePolicy:
        raise UnsafeWrite("the write policy must be exactly a WritePolicy")
    return _issue(scope, changes, undo_path, policy, "")


def authorize_typed(  # noqa: PLR0913 - every input of a write is explicit
    scope: WriteScope,
    changes: Sequence[Change],
    undo_path: Path,
    word: TypedWord,
    policy: WritePolicy,
    *,
    undo_sha256: str,
) -> WriteAuthorization:
    """Like :func:`authorize`, confirmed by a typed word and bound to the undo file's content.

    Raises:
        UnsafeWrite: The word does not match, the undo hash is not a SHA-256, the policy is not
            exactly a ``WritePolicy``, or a change is outside the policy.
    """
    if not word.matches():
        raise UnsafeWrite("the confirmation word does not match; nothing is written")
    if len(undo_sha256) != SHA256_HEX_LENGTH or not all(
        c in "0123456789abcdef" for c in undo_sha256
    ):
        raise UnsafeWrite("the undo file's hash is missing; nothing is written")
    if type(policy) is not WritePolicy:
        raise UnsafeWrite("the write policy must be exactly a WritePolicy")
    return _issue(scope, changes, undo_path, policy, undo_sha256)


def verify(auth: object) -> WriteAuthorization:
    """Return ``auth`` if it is an untouched authorization issued by :func:`authorize`.

    Raises:
        UnsafeWrite: ``auth`` is not exactly a ``WriteAuthorization``, its policy is not exactly
            a ``WritePolicy``, or its content does not match its seal.
    """
    if type(auth) is not WriteAuthorization:
        raise UnsafeWrite("the writer needs a WriteAuthorization from safety.authorize()")
    if type(auth.policy) is not WritePolicy:
        raise UnsafeWrite("the authorization carries a policy that is not exactly a WritePolicy")
    expected = _seal(auth.scope, auth.changes, auth.undo_path, auth.policy, auth.undo_sha256)
    if not hmac.compare_digest(auth.seal, expected):
        raise UnsafeWrite("the authorization was not issued by safety.authorize() or was altered")
    return auth
