# receipt-gate

<!-- mcp-name: io.github.adekvatologiy/receipt-gate -->

Agents say "done". This checks.

An agent reporting completion is not evidence of completion. `receipt-gate`
sits between the claim and the record: the claim is held until an
independent adapter observes the promised state in reality.

**Why this exists.** Per-step reliability multiplies. At 85% per step, a
10-step workflow finishes end to end about 20% of the time. The usual
answer is retries, checkpoints and idempotency keys. Those protect you
from doing a thing twice. They do not establish that the thing happened at
all.

The gate is on the sentence, not on the action.

## What it does

- A **promise** names one observable future state, a deadline, a damage
  ceiling, and which adapter will check it.
- A **capability proxy** opens only the actions that promise authorises,
  one call at a time, and returns the exact boundary when it refuses.
- A **receipt adapter** observes that state again, from a source that is
  not the agent's own report.
- The **speech gate** holds `done` — and any unrelated new promise —
  until the open promise is settled.
- What does not match is kept as a **remainder**: the exact gap, what can
  no longer be treated as finished, what authority is left, and who may
  accept, cancel or reassign it.

Four terminal states, and they do not mix: `fulfilled`,
`cancelled_by_owner`, `impossible_in_scope`, `handed_over`. Handed over is
not fulfilled.

## What it does not do

It gives the agent no goal, no legitimacy and no authority. It does not
make a bad promise good — if the owner picked the wrong target, the gap
will be measured against the wrong target. It only refuses to let the
distance between stated and happened stay invisible.

No scoring, no leaderboard, no model in the loop. An adapter re-reads
state; it does not have an opinion.

## Install

Python 3.10+, standard library only, no dependencies.

```
git clone https://github.com/adekvatologiy/receipt-gate
cd receipt-gate
python -m examples.first_ride
```

## Thirty lines

```python
from receipt_gate import Ledger, Promise, SpeechGate, ManifestAdapter, Held

ledger = Ledger("ledger.jsonl")          # append-only, hash-chained
gate = SpeechGate(ledger)

promise = Promise(
    deal_id="D-1",
    promise_id="P-1",
    holder="agent-1",
    promised_state="manifest.json matches a fresh read of the sources",
    receipt_adapter="manifest+sha256",   # named before acting
    authority=["read:sources", "write:manifest.json"],
    damage_ceiling=["write:sources/*", "delete:*", "rename:*"],
    owner="result-owner",
)
gate.open_promise(promise)

# ... the agent works, then says it finished ...

try:
    gate.claim_done("P-1")
except Held as exc:
    print(exc)        # 'done' needs a fresh receipt from 'manifest+sha256';
                      # a report by the holder is not one

adapter = ManifestAdapter("manifest.json", sources)
adapter.before = adapter.snapshot()      # taken before the ride

settlement = gate.claim_done("P-1", adapter.observe())
print(settlement.state)                  # fulfilled — or a remainder is written
if settlement.remainder:
    print(settlement.remainder.gap)      # the exact fact that did not match
```

## The first ride

The example is deliberately boring and completely checkable: build a
manifest of seven immutable source files with size and SHA-256, under a
damage ceiling of "no source may be written, renamed or deleted". A
separate validator re-reads all seven afterwards and compares.

Four agents run against the same promise:

| agent | what happens |
| --- | --- |
| honest | writes a correct manifest, receipt matches, promise settles |
| claiming | writes nothing, says it finished — held, no receipt |
| sloppy | one stale digest — remainder with the exact mismatch, debt stays open |
| reckless | tries to touch a protected source — refused with the ceiling that refused it |

A manifest can also be internally consistent and still fail: the adapter
compares against the state recorded *before* the ride, so a source edited
behind the gate is seen even when the manifest agrees with the new bytes.

## As an MCP server

```
RECEIPT_GATE_LEDGER=./ledger.jsonl RECEIPT_GATE_ROOT=. receipt-gate
```

Ten tools over stdio, and the surface is the construction rather than a way
around it. `claim_done` is held every time, because a tool call by the
holder is a report by the holder; the reply names the adapter that can
answer instead. `settle` runs that adapter and returns what it saw.

| tool | what it does |
| --- | --- |
| `open_promise` | records one observable state, the authority, the ceiling, and the adapter with its own parameters |
| `request_capability` | one action, for one call — or the exact boundary that refused it |
| `record_action` | spends a grant; a second use is refused against the ledger |
| `claim_done` | held, every time, with the adapter named |
| `settle` | runs the adapter; returns `fulfilled` or a remainder with four facts |
| `cancel_by_owner` · `impossible_in_scope` · `hand_over` | the other three terminal states |
| `open_debts` · `verify_ledger` | what the deal still holds; whether the chain is intact |

Two things differ here from the in-process library, and both follow from
the protocol:

- **The adapter's own parameters are fixed when the promise is opened** and
  written to the ledger, so a later call cannot redirect the reading. In
  the library the caller builds the adapter at settlement time. This is
  stricter than `SPEC.md` 0.1 requires and is a candidate for 0.2.
- **Grants live on the ledger, not in memory.** MCP 2026-07-28 is
  stateless and restarts the process between calls; a right that
  evaporates with a process is not a right. Open promises are read back
  from the ledger for the same reason, so the debts survive a restart.

Protocol: MCP `2026-07-28` — `server/discover`, per-request `_meta`,
`resultType`, `ttlMs`/`cacheScope` — with the legacy `initialize`
handshake answered for clients still on `2025-11-25` or `2025-06-18`.
`server.json` is ready for the official registry.

## Adapters

Three are included, and each attests only what its own source observes.

| adapter | attests |
| --- | --- |
| `manifest+sha256` | a manifest parses, matches a fresh read, protected sources unchanged |
| `command+exit_code` | a command ran in a clean directory, its exit code, optionally a string in the output |
| `owner_acceptance` | the result owner accepted one named version digest |

Writing an adapter is work. A promise no adapter can answer is the failure
this is built to make visible.

## Specification

[`SPEC.md`](SPEC.md) is versioned separately — five records, four terminal
states, three escrowed rights, and the rule about what an adapter may
attest. It can be reimplemented in another language without reading this
code.

## Status

Version 0.2. Spec 0.1 — the code and the specification carry their own
numbers, and 0.2 added the MCP server without changing the specification.
The five parts and the server work, and 43 tests cover them. Not yet: a durable-execution adapter, and language
bindings beyond Python.

Three things worth breaking:

1. Is there a case where "the adapter re-reads state" collapses back into
   trusting the agent?
2. Composite promises currently split or carry several receipts. Is there a
   cleaner rule?
3. The failure mode expected but not yet produced: agents learning to
   write narrow, easily-settled promises. What would catch that early?

## Provenance

[`PROVENANCE.md`](PROVENANCE.md) says where the construction came from, what
is owed to whom, and the three things asked of anyone who carries it
further. None of it is enforceable; it is there because a technical record
that hides its own origin is worth less than one that states it.

## Licence

MIT for the code. See `LICENSE`. Copyright Shcherbina Vadim, 2026.
