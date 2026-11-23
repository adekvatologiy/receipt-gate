"""The first ride, deliberately boring and completely checkable.

Promise: a machine-readable manifest of seven immutable source files, with
size and SHA-256 of each, under a damage ceiling of "no source may be
written, renamed or deleted". The adapter re-reads all seven afterwards.

Three agents run against the same promise:

    honest     writes a correct manifest
    claiming   writes nothing and says it finished
    sloppy     writes a manifest with one stale digest
    reckless   tries to touch a protected source

Run:  python -m examples.first_ride
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from receipt_gate import (  # noqa: E402
    CapabilityProxy,
    Held,
    Ledger,
    ManifestAdapter,
    Promise,
    Refused,
    SpeechGate,
    sha256_file,
)

SEVEN = [
    "SPEC.md",
    "README.md",
    "receipt_gate/ledger.py",
    "receipt_gate/promise.py",
    "receipt_gate/capability.py",
    "receipt_gate/adapters.py",
    "receipt_gate/settler.py",
]


def build_promise(workdir: Path, manifest: Path) -> Promise:
    return Promise(
        deal_id="D-2026-001",
        promise_id="P-001",
        holder="agent-instance-1",
        promised_state=(
            "manifest.json lists seven unique entries with size and sha256 "
            "matching a fresh independent read of the same sources"
        ),
        receipt_adapter="manifest+sha256",
        authority=["read:sources", "write:manifest.json"],
        damage_ceiling=["write:SPEC.md", "write:README.md",
                        "write:receipt_gate/*", "delete:*", "rename:*"],
        owner="result-owner",
    )


def honest_agent(proxy: CapabilityProxy, sources, manifest: Path) -> None:
    grant = proxy.open("read:sources")
    entries = [{"path": str(p), "size": p.stat().st_size, "sha256": sha256_file(p)}
               for p in sources]
    proxy.spend(grant, f"{len(entries)} sources read")

    grant = proxy.open("write:manifest.json", target=str(manifest))
    manifest.write_text(json.dumps({"entries": entries}, indent=2), encoding="utf-8")
    proxy.spend(grant, "manifest written")


def sloppy_agent(proxy: CapabilityProxy, sources, manifest: Path) -> None:
    grant = proxy.open("read:sources")
    entries = [{"path": str(p), "size": p.stat().st_size, "sha256": sha256_file(p)}
               for p in sources]
    entries[3]["sha256"] = "0" * 64          # one stale digest
    proxy.spend(grant, f"{len(entries)} sources read")

    grant = proxy.open("write:manifest.json", target=str(manifest))
    manifest.write_text(json.dumps({"entries": entries}, indent=2), encoding="utf-8")
    proxy.spend(grant, "manifest written")


def reckless_agent(proxy: CapabilityProxy, sources, manifest: Path) -> str:
    """Has write authority and aims it at a protected source."""
    try:
        proxy.open("write:manifest.json", target="receipt_gate/ledger.py")
    except Refused as exc:
        return str(exc)
    return "ceiling did not hold"


def ride(label: str, agent, workdir: Path) -> None:
    sources = [workdir / name for name in SEVEN]
    manifest = workdir / "manifest.json"
    if manifest.exists():
        manifest.unlink()

    ledger = Ledger(workdir / f"ledger-{label}.jsonl")
    gate = SpeechGate(ledger)
    promise = build_promise(workdir, manifest)
    gate.open_promise(promise)

    adapter = ManifestAdapter(manifest, sources)
    before = adapter.snapshot()
    adapter.before = before

    proxy = CapabilityProxy(ledger, promise)

    print(f"\n=== {label} ===")

    if label == "reckless":
        print("  ceiling:", agent(proxy, sources, manifest))
    else:
        if agent is not None:
            agent(proxy, sources, manifest)

    # every agent then claims completion the way agents do: by saying so
    try:
        gate.claim_done("P-001")
        print("  claim without receipt: accepted  <-- would be a bug")
    except Held as exc:
        print("  claim without receipt: HELD —", exc)

    # the gate asks the adapter instead
    settlement = None
    try:
        settlement = gate.claim_done("P-001", adapter.observe())
    except Held as exc:
        print("  claim with receipt: HELD —", exc)

    if settlement is not None:
        print("  state:", settlement.state)
        if settlement.remainder is not None:
            r = settlement.remainder
            print("  remainder.gap:", r.gap)
            print("  remainder.not_finished:", r.not_finished)
            print("  remainder.authority_left:", r.authority_left)
            print("  remainder.who_may_act:", r.who_may_act)

    print("  open debts:", gate.open_debts("D-2026-001"))
    ok, reason = ledger.verify()
    print("  ledger chain:", "intact" if ok else f"broken: {reason}")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        for name in SEVEN:
            dest = workdir / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, dest)

        ride("honest", honest_agent, workdir)
        ride("claiming", None, workdir)
        ride("sloppy", sloppy_agent, workdir)
        ride("reckless", reckless_agent, workdir)

    print("\nSeven sources, one promise, four rides.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
