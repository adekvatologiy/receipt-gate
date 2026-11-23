"""Append-only event ledger with a hash chain.

A record is never rewritten. Correcting an earlier record is a new record
that names what it supersedes and on what basis.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

GENESIS = "0" * 64


def canon(obj: Any) -> str:
    """Canonical JSON: stable key order, no insignificant whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(prev_hash: str, body: Dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + canon(body)).encode("utf-8")).hexdigest()


class Ledger:
    """Events are added. Nothing already written is edited.

    Each record carries the hash of the one before it, so a silent edit
    anywhere in the file is detectable by verify().
    """

    def __init__(self, path: str | Path, clock=time.time) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self._clock = clock

    # ---- reading -------------------------------------------------------

    def records(self) -> Iterator[Dict[str, Any]]:
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def all(self) -> List[Dict[str, Any]]:
        return list(self.records())

    def head(self) -> Tuple[int, str]:
        """(sequence of last record, its hash). (0, GENESIS) when empty."""
        seq, last = 0, GENESIS
        for rec in self.records():
            seq, last = rec["seq"], rec["hash"]
        return seq, last

    def find(self, kind: Optional[str] = None, **match: Any) -> List[Dict[str, Any]]:
        out = []
        for rec in self.records():
            if kind is not None and rec["kind"] != kind:
                continue
            body = rec.get("body", {})
            if all(body.get(k) == v for k, v in match.items()):
                out.append(rec)
        return out

    # ---- writing -------------------------------------------------------

    def append(self, kind: str, body: Dict[str, Any]) -> Dict[str, Any]:
        seq, prev = self.head()
        core = {"seq": seq + 1, "kind": kind, "at": self._clock(), "body": body}
        rec = dict(core)
        rec["prev"] = prev
        rec["hash"] = digest(prev, core)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(canon(rec) + "\n")
        return rec

    # ---- integrity -----------------------------------------------------

    def verify(self) -> Tuple[bool, Optional[str]]:
        """(True, None) when the chain holds; (False, reason) at the first break."""
        prev = GENESIS
        expected_seq = 1
        for rec in self.records():
            if rec.get("seq") != expected_seq:
                return False, f"sequence break at {rec.get('seq')!r}, expected {expected_seq}"
            if rec.get("prev") != prev:
                return False, f"broken link at seq {expected_seq}"
            core = {k: rec[k] for k in ("seq", "kind", "at", "body")}
            if rec.get("hash") != digest(prev, core):
                return False, f"record edited at seq {expected_seq}"
            prev = rec["hash"]
            expected_seq += 1
        return True, None
