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

# Answers that confirm a write, as typed by the operator at the prompt.
CONFIRMATIONS: frozenset[str] = frozenset({"y", "yes", "д", "да"})
_SEAL_KEY = secrets.token_bytes(32)


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
    """

    scope: WriteScope
    changes: tuple[Change, ...]
    undo_path: Path
    policy: WritePolicy
    seal: str


def _seal(
    scope: WriteScope, changes: tuple[Change, ...], undo_path: Path, policy: WritePolicy
) -> str:
    content = repr(
        (
            str(scope),
            tuple((c.path, type(c.new_value).__name__, repr(c.new_value)) for c in changes),
            str(undo_path),
            type(policy.laws).__qualname__,
            tuple(sorted(policy.matrices)),
            repr(policy.limits),
            policy.allow_unverified_writes,
        )
    )
    return hmac.new(_SEAL_KEY, content.encode(), hashlib.sha256).hexdigest()


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
        UnsafeWrite: The operator did not confirm, the policy is not exactly a ``WritePolicy``,
            or a change is outside the policy.
    """
    if not is_confirmation(confirmation):
        raise UnsafeWrite("the operator did not confirm the write")
    if type(policy) is not WritePolicy:
        raise UnsafeWrite("the write policy must be exactly a WritePolicy")
    plan = tuple(changes)
    for change in plan:
        policy.validate(change.path, change.new_value, scope)
    return WriteAuthorization(scope, plan, undo_path, policy, _seal(scope, plan, undo_path, policy))


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
    expected = _seal(auth.scope, auth.changes, auth.undo_path, auth.policy)
    if not hmac.compare_digest(auth.seal, expected):
        raise UnsafeWrite("the authorization was not issued by safety.authorize() or was altered")
    return auth
