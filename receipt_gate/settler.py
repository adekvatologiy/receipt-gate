"""The settler.

It puts a promise beside an observation. Match settles the promise and
returns the escrowed rights. No match is written as a remainder, which
stays a working part of the deal.

A failed ride is a settled ride when the deal has no silent promises left.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .ledger import Ledger
from .promise import (
    FULFILLED,
    HANDED_OVER,
    IMPOSSIBLE_IN_SCOPE,
    CANCELLED_BY_OWNER,
    Observation,
    Promise,
    Remainder,
)


@dataclass
class Settlement:
    promise_id: str
    state: str
    rights_returned: List[str]
    remainder: Optional[Remainder] = None

    @property
    def fulfilled(self) -> bool:
        return self.state == FULFILLED


class Settler:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    # ---- the ordinary path ---------------------------------------------

    def settle(self, promise: Promise, observation: Observation) -> Settlement:
        self.ledger.append("receipt", {"promise_id": promise.promise_id,
                                       **observation.as_body()})

        if observation.matches:
            self.ledger.append(
                "settled",
                {"promise_id": promise.promise_id, "state": FULFILLED,
                 "rights_returned": list(promise.escrowed_rights)},
            )
            return Settlement(promise.promise_id, FULFILLED,
                              list(promise.escrowed_rights))

        remainder = _remainder_from(promise, observation)
        self.ledger.append("remainder", remainder.as_body())
        return Settlement(promise.promise_id, "open", [], remainder)

    # ---- the other three ends ------------------------------------------

    def cancel_by_owner(self, promise: Promise, owner: str,
                        version: Optional[str] = None) -> Settlement:
        self.ledger.append(
            "settled",
            {"promise_id": promise.promise_id, "state": CANCELLED_BY_OWNER,
             "owner": owner, "version": version},
        )
        return Settlement(promise.promise_id, CANCELLED_BY_OWNER,
                          list(promise.escrowed_rights))

    def impossible_in_scope(self, promise: Promise, observation: Observation,
                            boundary: str) -> Settlement:
        self.ledger.append("receipt", {"promise_id": promise.promise_id,
                                       **observation.as_body()})
        remainder = _remainder_from(promise, observation)
        remainder.explanation = f"impossible within authority: {boundary}"
        self.ledger.append("remainder", remainder.as_body())
        self.ledger.append(
            "settled",
            {"promise_id": promise.promise_id, "state": IMPOSSIBLE_IN_SCOPE,
             "boundary": boundary},
        )
        return Settlement(promise.promise_id, IMPOSSIBLE_IN_SCOPE,
                          list(promise.escrowed_rights), remainder)

    def hand_over(self, promise: Promise, remainder: Remainder,
                  new_holder: str, new_promise: Promise) -> Settlement:
        """Taking a remainder does not make the new holder the author of the
        old promise. It creates a new promise linked to the old imprint.

        Handed over is not fulfilled.
        """
        self.ledger.append(
            "handed_over",
            {"promise_id": promise.promise_id, "to": new_holder,
             "new_promise_id": new_promise.promise_id,
             "remainder": remainder.as_body()},
        )
        self.ledger.append("promise", new_promise.as_body())
        self.ledger.append(
            "settled",
            {"promise_id": promise.promise_id, "state": HANDED_OVER,
             "successor": new_promise.promise_id},
        )
        return Settlement(promise.promise_id, HANDED_OVER,
                          [r for r in promise.escrowed_rights], remainder)

    # ---- reading the deal ----------------------------------------------

    def open_promises(self, deal_id: Optional[str] = None) -> List[str]:
        opened: Dict[str, Dict[str, Any]] = {}
        for rec in self.ledger.records():
            body = rec.get("body", {})
            if rec["kind"] == "promise":
                if deal_id is None or body.get("deal_id") == deal_id:
                    opened[body["promise_id"]] = body
            elif rec["kind"] == "settled":
                opened.pop(body.get("promise_id"), None)
        return sorted(opened)


def _remainder_from(promise: Promise, observation: Observation) -> Remainder:
    problems = observation.observed_state.get("problems")
    if isinstance(problems, list) and problems:
        gap = "; ".join(str(p) for p in problems)
    else:
        gap = observation.note or f"observed state does not match: {observation.observed_state}"
    return Remainder(
        promise_id=promise.promise_id,
        gap=gap,
        not_finished=promise.promised_state,
        authority_left=list(promise.authority),
        who_may_act=[promise.owner or "result owner", promise.holder],
    )
