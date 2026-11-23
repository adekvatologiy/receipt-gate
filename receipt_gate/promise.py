"""A promise, an observation of reality, a settlement, a remainder.

A promise is admissible only when the named adapter can answer it with a
fact. "Make it good" is inadmissible. "The result owner accepted the
version with this digest" is admissible, because the source of the
receipt is named in advance.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

# Terminal states. They do not mix. Handed over is not fulfilled.
OPEN = "open"
FULFILLED = "fulfilled"
CANCELLED_BY_OWNER = "cancelled_by_owner"
IMPOSSIBLE_IN_SCOPE = "impossible_in_scope"
HANDED_OVER = "handed_over"

TERMINAL = (FULFILLED, CANCELLED_BY_OWNER, IMPOSSIBLE_IN_SCOPE, HANDED_OVER)

# Rights held in escrow while a promise is open.
CLAIM_DONE = "claim_done"
OPEN_UNRELATED_PROMISE = "open_unrelated_promise"
SILENT_HANDOFF = "silent_handoff"

DEFAULT_ESCROW = (CLAIM_DONE, OPEN_UNRELATED_PROMISE, SILENT_HANDOFF)


class InadmissiblePromise(ValueError):
    """Raised when nothing named can answer the promise with a fact."""


@dataclass
class Promise:
    deal_id: str
    promise_id: str
    holder: str
    promised_state: str
    receipt_adapter: str
    authority: List[str]
    damage_ceiling: List[str] = field(default_factory=list)
    expires_at: Optional[float] = None
    escrowed_rights: List[str] = field(default_factory=lambda: list(DEFAULT_ESCROW))
    owner: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.promised_state.strip():
            raise InadmissiblePromise("promised_state is empty")
        if not self.receipt_adapter.strip():
            raise InadmissiblePromise(
                "no receipt adapter named: nothing can answer this promise with a fact"
            )
        if not self.authority:
            raise InadmissiblePromise("no authority requested: the promise cannot act")

    def as_body(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Observation:
    """What an adapter saw, from a source that is not the holder's report."""

    source: str
    observed_at: float
    observed_state: Dict[str, Any]
    matches: bool
    evidence_sha256: Optional[str] = None
    note: Optional[str] = None

    def as_body(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Remainder:
    """The exact gap, kept as a working part of the deal.

    Four facts, and an explanation never replaces them.
    """

    promise_id: str
    gap: str                      # which observable fact did not match
    not_finished: str             # what can no longer be treated as done
    authority_left: List[str]     # which authority is still available
    who_may_act: List[str]        # who may accept, cancel, hand over or change it
    explanation: Optional[str] = None

    def as_body(self) -> Dict[str, Any]:
        return asdict(self)
