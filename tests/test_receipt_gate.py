"""Tests. Each one names the thing it refuses to let pass silently."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from receipt_gate import (  # noqa: E402
    CANCELLED_BY_OWNER,
    CapabilityProxy,
    FULFILLED,
    HANDED_OVER,
    Held,
    IMPOSSIBLE_IN_SCOPE,
    InadmissiblePromise,
    Ledger,
    ManifestAdapter,
    Observation,
    Promise,
    Refused,
    Settler,
    SpeechGate,
    sha256_file,
)


# ---- fixtures ----------------------------------------------------------

@pytest.fixture
def sources(tmp_path: Path):
    paths = []
    for i in range(3):
        p = tmp_path / f"source_{i}.txt"
        p.write_text(f"source {i}\n", encoding="utf-8")
        paths.append(p)
    return paths


def make_promise(**over) -> Promise:
    kwargs = dict(
        deal_id="D-1",
        promise_id="P-1",
        holder="agent-1",
        promised_state="manifest.json matches a fresh read of the sources",
        receipt_adapter="manifest+sha256",
        authority=["read:sources", "write:manifest.json"],
        damage_ceiling=["write:source_*", "delete:*"],
        owner="owner-1",
    )
    kwargs.update(over)
    return Promise(**kwargs)


def write_manifest(manifest: Path, sources, break_index=None) -> None:
    entries = [{"path": str(p), "size": p.stat().st_size, "sha256": sha256_file(p)}
               for p in sources]
    if break_index is not None:
        entries[break_index]["sha256"] = "0" * 64
    manifest.write_text(json.dumps({"entries": entries}), encoding="utf-8")


# ---- the ledger --------------------------------------------------------

def test_chain_holds_and_detects_an_edit(tmp_path: Path):
    ledger = Ledger(tmp_path / "l.jsonl")
    ledger.append("a", {"n": 1})
    ledger.append("b", {"n": 2})
    ledger.append("c", {"n": 3})
    assert ledger.verify() == (True, None)

    lines = (tmp_path / "l.jsonl").read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["body"]["n"] = 99
    lines[1] = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
    (tmp_path / "l.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, reason = ledger.verify()
    assert ok is False
    assert "edited" in reason


# ---- admissibility -----------------------------------------------------

def test_a_promise_without_an_adapter_is_inadmissible():
    with pytest.raises(InadmissiblePromise):
        make_promise(receipt_adapter="")


def test_a_promise_without_authority_is_inadmissible():
    with pytest.raises(InadmissiblePromise):
        make_promise(authority=[])


# ---- the gate on the sentence ------------------------------------------

def test_done_without_a_receipt_is_held(tmp_path: Path):
    gate = SpeechGate(Ledger(tmp_path / "l.jsonl"))
    gate.open_promise(make_promise())
    with pytest.raises(Held) as exc:
        gate.claim_done("P-1")
    assert "receipt" in str(exc.value)


def test_a_receipt_from_the_wrong_source_is_held(tmp_path: Path):
    gate = SpeechGate(Ledger(tmp_path / "l.jsonl"))
    gate.open_promise(make_promise())
    foreign = Observation(source="command+exit_code", observed_at=0.0,
                          observed_state={"exit_code": 0}, matches=True)
    with pytest.raises(Held):
        gate.claim_done("P-1", foreign)


def test_ordinary_speech_passes_without_escrow(tmp_path: Path):
    gate = SpeechGate(Ledger(tmp_path / "l.jsonl"))
    gate.open_promise(make_promise())
    assert gate.say("still working on it") == "still working on it"


# ---- match and mismatch ------------------------------------------------

def test_match_settles_and_returns_the_rights(tmp_path: Path, sources):
    manifest = tmp_path / "manifest.json"
    gate = SpeechGate(Ledger(tmp_path / "l.jsonl"))
    promise = gate.open_promise(make_promise())
    adapter = ManifestAdapter(manifest, sources)
    adapter.before = adapter.snapshot()

    write_manifest(manifest, sources)
    settlement = gate.claim_done("P-1", adapter.observe())

    assert settlement.state == FULFILLED
    assert "claim_done" in settlement.rights_returned
    assert gate.open_debts("D-1") == []


def test_mismatch_keeps_a_remainder_with_four_facts(tmp_path: Path, sources):
    manifest = tmp_path / "manifest.json"
    gate = SpeechGate(Ledger(tmp_path / "l.jsonl"))
    promise = gate.open_promise(make_promise())
    adapter = ManifestAdapter(manifest, sources)
    adapter.before = adapter.snapshot()

    write_manifest(manifest, sources, break_index=1)
    settlement = gate.claim_done("P-1", adapter.observe())

    assert settlement.state != FULFILLED
    r = settlement.remainder
    assert r is not None
    assert "digest mismatch" in r.gap
    assert r.not_finished == promise.promised_state
    assert r.authority_left == promise.authority
    assert "owner-1" in r.who_may_act
    assert gate.open_debts("D-1") == ["P-1"]


def test_a_changed_protected_source_is_seen_even_when_the_manifest_agrees(
    tmp_path: Path, sources
):
    """The manifest can be internally consistent and still fail.

    The adapter compares against the state recorded before the ride.
    """
    manifest = tmp_path / "manifest.json"
    gate = SpeechGate(Ledger(tmp_path / "l.jsonl"))
    gate.open_promise(make_promise())
    adapter = ManifestAdapter(manifest, sources)
    adapter.before = adapter.snapshot()

    sources[0].write_text("edited behind the gate\n", encoding="utf-8")
    write_manifest(manifest, sources)          # manifest matches the new bytes

    settlement = gate.claim_done("P-1", adapter.observe())
    assert settlement.state != FULFILLED
    assert "protected source changed" in settlement.remainder.gap


# ---- the capability proxy ----------------------------------------------

def test_an_action_outside_authority_is_refused_with_its_boundary(tmp_path: Path):
    ledger = Ledger(tmp_path / "l.jsonl")
    proxy = CapabilityProxy(ledger, make_promise())
    with pytest.raises(Refused) as exc:
        proxy.open("delete:everything")
    assert "outside this promise's authority" in str(exc.value)


def test_the_damage_ceiling_refuses_a_protected_target(tmp_path: Path):
    ledger = Ledger(tmp_path / "l.jsonl")
    promise = make_promise(authority=["read:sources", "write:source_0.txt"])
    proxy = CapabilityProxy(ledger, promise)
    with pytest.raises(Refused) as exc:
        proxy.open("write:source_0.txt", target="source_0.txt")
    assert "damage ceiling" in str(exc.value)


def test_a_capability_is_opened_for_one_call(tmp_path: Path):
    ledger = Ledger(tmp_path / "l.jsonl")
    proxy = CapabilityProxy(ledger, make_promise())
    grant = proxy.open("read:sources")
    proxy.spend(grant)
    with pytest.raises(Refused):
        proxy.spend(grant)


# ---- the other three ends ----------------------------------------------

def test_unrelated_work_is_held_while_a_debt_is_open(tmp_path: Path):
    gate = SpeechGate(Ledger(tmp_path / "l.jsonl"))
    gate.open_promise(make_promise())
    with pytest.raises(Held) as exc:
        gate.open_promise(make_promise(promise_id="P-2",
                                       promised_state="something else entirely"))
    assert "open debt" in str(exc.value)


def test_cancelled_by_owner_is_not_a_failed_fulfilment(tmp_path: Path):
    ledger = Ledger(tmp_path / "l.jsonl")
    gate = SpeechGate(ledger)
    promise = gate.open_promise(make_promise())
    settlement = Settler(ledger).cancel_by_owner(promise, "owner-1", version="v1")
    assert settlement.state == CANCELLED_BY_OWNER
    assert gate.open_debts("D-1") == []


def test_impossible_in_scope_keeps_the_boundary(tmp_path: Path, sources):
    ledger = Ledger(tmp_path / "l.jsonl")
    gate = SpeechGate(ledger)
    promise = gate.open_promise(make_promise())
    adapter = ManifestAdapter(tmp_path / "manifest.json", sources)
    adapter.before = adapter.snapshot()
    settlement = Settler(ledger).impossible_in_scope(
        promise, adapter.observe(), boundary="no authority to read source_2"
    )
    assert settlement.state == IMPOSSIBLE_IN_SCOPE
    assert "impossible within authority" in settlement.remainder.explanation
    assert gate.open_debts("D-1") == []


def test_handed_over_is_not_fulfilled(tmp_path: Path, sources):
    ledger = Ledger(tmp_path / "l.jsonl")
    gate = SpeechGate(ledger)
    promise = gate.open_promise(make_promise())
    adapter = ManifestAdapter(tmp_path / "manifest.json", sources)
    adapter.before = adapter.snapshot()

    settler = Settler(ledger)
    first = settler.settle(promise, adapter.observe())
    assert first.remainder is not None

    successor = make_promise(promise_id="P-1b", holder="agent-2")
    handed = settler.hand_over(promise, first.remainder, "agent-2", successor)

    assert handed.state == HANDED_OVER
    assert handed.state != FULFILLED
    kinds = [r["kind"] for r in ledger.all()]
    assert "handed_over" in kinds
    assert ledger.verify() == (True, None)


# ---- the whole ride ----------------------------------------------------

def test_a_failed_ride_leaves_no_silent_promise(tmp_path: Path, sources):
    manifest = tmp_path / "manifest.json"
    ledger = Ledger(tmp_path / "l.jsonl")
    gate = SpeechGate(ledger)
    gate.open_promise(make_promise())
    adapter = ManifestAdapter(manifest, sources)
    adapter.before = adapter.snapshot()

    write_manifest(manifest, sources, break_index=0)
    gate.claim_done("P-1", adapter.observe())

    assert gate.open_debts("D-1") == ["P-1"]          # the debt is visible
    remainders = ledger.find("remainder", promise_id="P-1")
    assert len(remainders) == 1                        # and written once
    assert ledger.verify() == (True, None)
