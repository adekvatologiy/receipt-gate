# Changelog

The code and the specification carry their own numbers. A code release may
leave the specification where it is, and does here.

## 0.2.0 — 22 November 2026 · specification 0.1

Added an MCP server, so the gate can stand in front of a model's own
sentence rather than only inside a Python process.

- `receipt_gate.mcp_server` — ten tools over stdio, stdlib only. The tool
  surface is the construction: `claim_done` is held every time, and
  `settle` runs the adapter the promise named.
- Protocol MCP `2026-07-28`: `server/discover`, per-request `_meta`,
  `resultType`, `ttlMs`/`cacheScope`. The legacy `initialize` handshake is
  answered for clients still on `2025-11-25` or `2025-06-18`.
- `server.json` for the official MCP registry, and a `receipt-gate`
  console entry point so `uvx receipt-gate` starts the server.
- `SpeechGate.restore` and `SpeechGate.forget` — a gate rebuilt from the
  ledger comes back in step with it, writing nothing. A stateless protocol
  restarts the process between calls and the debts have to survive that.
- Two rules the protocol made stricter than the library:
  - an adapter's own parameters are fixed when the promise is opened and
    written to the ledger, so a later call cannot redirect the reading;
  - capability grants live on the ledger rather than in memory.

  Both are candidates for specification 0.2. Neither changes 0.1.
- 26 further tests, driven over the wire rather than through the Python
  call underneath it. A CI step sends real messages to the process and
  checks that nothing but MCP messages reaches stdout.

## 0.1.0 — 22 November 2026 · specification 0.1

First cut, not released.

- `Ledger`, `Promise`, `CapabilityProxy`, three adapters, `Settler`,
  `SpeechGate`.
- `SPEC.md` 0.1 — five records, four terminal states, three escrowed
  rights, and the rule that one receipt attests only what its own source
  observes.
- `examples/first_ride.py` — seven immutable sources, one promise, four
  agents, the same measure for each.
- 17 tests, and a CI step that keeps the package inside the standard
  library.
