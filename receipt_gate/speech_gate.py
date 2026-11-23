"""The speech gate.

A message that creates no external promise and claims no completion
passes without escrow. A claim of completion, and an unrelated new
promise, are held while a promise is open.

The gate is on the sentence, not on the action.
"""

from __future__ import annotations

from typing import List, Optional

from .ledger import Ledger
from .promise import (
    CLAIM_DONE,
    OPEN_UNRELATED_PROMISE,
    Observation,
    Promise,
)
from .settler import Settlement, Settler


class Held(RuntimeError):
    """Raised when a claim or a new promise is held by an open debt."""


class SpeechGate:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self.settler = Settler(ledger)
        self._promises: dict[str, Promise] = {}

    # ---- passing through -----------------------------------------------

    def say(self, text: str) -> str:
        """Ordinary speech. No promise, no claim, no escrow."""
        self.ledger.append("said", {"text": text})
        return text

    # ---- opening a promise ---------------------------------------------

    def open_promise(self, promise: Promise) -> Promise:
        open_now = self.settler.open_promises(promise.deal_id)
        unrelated = [p for p in open_now if p != promise.promise_id]
        if unrelated:
            held_by = self._promises.get(unrelated[0])
            if held_by is None or OPEN_UNRELATED_PROMISE in held_by.escrowed_rights:
                reason = (
                    f"open debt {unrelated[0]!r} holds the right to open unrelated work; "
                    f"settle it, cancel it, record it impossible, or hand it over"
                )
                self.ledger.append(
                    "speech_held",
                    {"attempt": "open_unrelated_promise",
                     "promise_id": promise.promise_id, "blocked_by": unrelated,
                     "reason": reason},
                )
                raise Held(reason)

        self.ledger.append("promise", promise.as_body())
        self._promises[promise.promise_id] = promise
        return promise

    # ---- claiming completion -------------------------------------------

    def claim_done(self, promise_id: str,
                   observation: Optional[Observation] = None) -> Settlement:
        """A claim of completion asks the adapter, not the holder.

        Passing no observation is the common case in the wild: an agent
        says it finished. The gate holds that and says what is missing.
        """
        promise = self._promises.get(promise_id)
        if promise is None:
            raise Held(f"no open promise {promise_id!r} on record")

        if observation is None:
            reason = (
                f"'done' needs a fresh receipt from {promise.receipt_adapter!r}; "
                f"a report by the holder is not one"
            )
            self.ledger.append(
                "speech_held",
                {"attempt": "claim_done", "promise_id": promise_id, "reason": reason},
            )
            raise Held(reason)

        if observation.source != promise.receipt_adapter:
            reason = (
                f"receipt came from {observation.source!r}, promise names "
                f"{promise.receipt_adapter!r}"
            )
            self.ledger.append(
                "speech_held",
                {"attempt": "claim_done", "promise_id": promise_id, "reason": reason},
            )
            raise Held(reason)

        settlement = self.settler.settle(promise, observation)
        if settlement.fulfilled:
            self._promises.pop(promise_id, None)
            self.ledger.append(
                "said",
                {"text": "done", "promise_id": promise_id,
                 "receipt": observation.evidence_sha256},
            )
        else:
            self.ledger.append(
                "speech_held",
                {"attempt": "claim_done", "promise_id": promise_id,
                 "reason": "receipt did not match; remainder recorded"},
            )
        return settlement

    # ---- rebuilding from the ledger -------------------------------------

    def restore(self, promise: Promise) -> None:
        """Register a promise already written to the ledger. Writes nothing.

        A stateless protocol restarts the process between calls, and the
        debts have to survive that. The ledger is the record; this puts a
        rebuilt gate back in step with it.
        """
        self._promises[promise.promise_id] = promise

    def forget(self, promise_id: str) -> None:
        """Drop a promise from the live set. The ledger keeps its records."""
        self._promises.pop(promise_id, None)

    # ---- state ----------------------------------------------------------

    def open_debts(self, deal_id: Optional[str] = None) -> List[str]:
        return self.settler.open_promises(deal_id)

    def rights_held(self, promise_id: str) -> List[str]:
        promise = self._promises.get(promise_id)
        if promise is None:
            return []
        return list(promise.escrowed_rights)

    def may_claim_done(self, promise_id: str) -> bool:
        return CLAIM_DONE not in self.rights_held(promise_id)
