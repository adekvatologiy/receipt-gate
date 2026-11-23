"""The capability proxy.

It does not solve the task. It compares a requested action with the
promise and opens a capability for one call, or returns the exact
boundary that refused it.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .ledger import Ledger
from .promise import Promise


class Refused(PermissionError):
    """Raised with the exact boundary that refused the action."""


@dataclass
class Grant:
    token: str
    action: str
    promise_id: str
    spent: bool = False


class CapabilityProxy:
    """Capabilities belong to the promise, not to the holder's role."""

    def __init__(self, ledger: Ledger, promise: Promise) -> None:
        self.ledger = ledger
        self.promise = promise
        self._grants: Dict[str, Grant] = {}

    # ---- granting ------------------------------------------------------

    def open(self, action: str, target: Optional[str] = None) -> Grant:
        if action not in self.promise.authority:
            reason = (
                f"action {action!r} is outside this promise's authority "
                f"{sorted(self.promise.authority)}"
            )
            self.ledger.append(
                "capability_refused",
                {"promise_id": self.promise.promise_id, "action": action,
                 "target": target, "boundary": reason},
            )
            raise Refused(reason)

        if target is not None:
            for rule in self.promise.damage_ceiling:
                if _violates(rule, action, target):
                    reason = f"damage ceiling refuses it: {rule!r}"
                    self.ledger.append(
                        "capability_refused",
                        {"promise_id": self.promise.promise_id, "action": action,
                         "target": target, "boundary": reason},
                    )
                    raise Refused(reason)

        grant = Grant(token=secrets.token_hex(8), action=action,
                      promise_id=self.promise.promise_id)
        self._grants[grant.token] = grant
        self.ledger.append(
            "capability_opened",
            {"promise_id": self.promise.promise_id, "action": action,
             "target": target, "token": grant.token},
        )
        return grant

    # ---- spending ------------------------------------------------------

    def spend(self, grant: Grant, result: Any = None) -> None:
        """A capability is opened for one call. A second use is refused."""
        held = self._grants.get(grant.token)
        if held is None or held.spent:
            reason = "capability already spent; open a new one"
            self.ledger.append(
                "capability_refused",
                {"promise_id": self.promise.promise_id, "action": grant.action,
                 "boundary": reason},
            )
            raise Refused(reason)
        held.spent = True
        self.ledger.append(
            "action_taken",
            {"promise_id": self.promise.promise_id, "action": grant.action,
             "token": grant.token, "result": _short(result)},
        )


WRITING = ("write", "rename", "delete", "move", "truncate")


def _violates(rule: str, action: str, target: str) -> bool:
    """Damage ceiling rules are written as `verb:glob`, e.g. `write:sources/*`.

    An action is also `verb:what`, e.g. `write:manifest.json`, so the verb
    is taken from its prefix. A rule verb of `*` or `any` covers every
    writing verb; a rule with no verb at all does the same.
    """
    from fnmatch import fnmatch

    action_verb = action.split(":", 1)[0].strip()

    if ":" in rule:
        rule_verb, pattern = rule.split(":", 1)
        rule_verb = rule_verb.strip()
        verbs = set(WRITING) if rule_verb in ("*", "any") else {rule_verb}
    else:
        verbs, pattern = set(WRITING), rule

    if action_verb not in verbs:
        return False

    pattern = pattern.strip()
    return fnmatch(target, pattern) or fnmatch(target, f"*/{pattern}")


def _short(value: Any, limit: int = 240) -> Any:
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "…"
