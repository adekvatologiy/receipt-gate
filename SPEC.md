# receipt-gate — specification

**Version 0.1.** The specification is versioned separately from the
implementation so it can be reimplemented in another language without
reading this one.

## 0. What this governs

A promise of an external change, and the distance between what was said
and what can be found without trusting the one who said it.

It governs five records, four terminal states, three escrowed rights, and
one rule about what an adapter may attest.

It does not govern goals, correctness of goals, or authority to act.

## 1. Records

Records are added. A record already written is never edited. Correcting an
earlier record is a new record naming what it supersedes and on what
basis.

### 1.1 Deal

| field | meaning |
| --- | --- |
| `deal_id` | identifier |
| `boundary` | the real change in scope |
| `owner` | who owns the result |
| `damage_ceiling` | what may not change |

Must not be substituted by: the topic of conversation in place of the
subject of action.

### 1.2 Promise

| field | meaning |
| --- | --- |
| `promise_id` | identifier |
| `holder` | who holds it now |
| `promised_state` | **one** observable future state |
| `receipt_adapter` | which source will confirm it |
| `authority` | the actions required, and no others |
| `damage_ceiling` | what may not change while acting |
| `expires_at` | deadline or stop condition |

Must not be substituted by: intention in place of result.

**Admissibility.** A promise is admissible when the named adapter can
answer it with a fact. `make it good` is inadmissible. `the result owner
accepted the version with digest X` is admissible, because the source of
the receipt is named in advance.

A promise need not state *how*. It must state what will become true, the
boundary of what is allowed, and how it will be found independently.

### 1.3 Escrow

While a promise is open the holder gives up three rights:

- `claim_done` — declaring the promise finished without a receipt
- `open_unrelated_promise` — starting unrelated work while this one is invisible
- `silent_handoff` — passing work on without recording a remainder and a new holder

Still available: stopping, recording impossibility, requesting wider
authority, returning the deal to its owner. Not available: making an open
promise cease to exist.

Rights return separately. A matching receipt returns `claim_done`. An
owner's accepted refusal returns the right to stop. An explicit handover
returns the right to leave — and does not turn a remainder into a
fulfilment.

### 1.4 Receipt

| field | meaning |
| --- | --- |
| `source` | which adapter observed |
| `observed_at` | when |
| `observed_state` | what was found |
| `matches` | whether it matches the promised state |
| `evidence_sha256` | imprint of the evidence |

Must not be substituted by: a report by the holder in place of a meeting
with reality.

**One receipt attests only what its own source can observe.** A successful
exit code does not establish human acceptance. A human saying `fine` does
not establish that protected files are unchanged. A composite promise
either carries several receipts or splits into several promises.

### 1.5 Remainder

| field | meaning |
| --- | --- |
| `gap` | which observable fact did not match |
| `not_finished` | what can no longer be treated as done |
| `authority_left` | which authority is still available |
| `who_may_act` | who may accept, cancel, hand over or change it |

Must not be substituted by: failure in place of the obligation
disappearing.

An explanation may be attached. It never replaces those four facts.

## 2. Terminal states

A ride is settled when the deal holds no silent promises. Every promise
created must end in exactly one of:

| state | required fact |
| --- | --- |
| `fulfilled` | an independent receipt matched the promised state |
| `cancelled_by_owner` | the result owner explicitly released it, naming a version |
| `impossible_in_scope` | a receipt of impossibility and the authority boundary are kept |
| `handed_over` | a remainder was accepted by a new holder under a new promise |

They do not mix.

- `handed_over` is **not** `fulfilled`.
- `cancelled_by_owner` is **not** a failed fulfilment.
- `impossible_in_scope` is **not** a false success.

A settled ride need not be successful by its original intent. It must
leave reality and the word in a settled relation.

## 3. The gate

The gate is on the sentence, not on the action.

| what the holder emits | what happens |
| --- | --- |
| speech creating no external promise and no claim | passes, no escrow |
| a request for an action inside the promise, within the ceiling | one capability, for one call |
| a request for an action outside authority | refused, with the exact boundary returned |
| an irreversible action with no adapter defined | held until an adapter or the owner's consent exists |
| a claim of completion | a fresh receipt is required; a retelling of the work is not one |
| an unrelated new promise while a debt is open | the open remainder and the ways to settle it are shown |

A capability is opened for an action, not forever. Therefore the learning
happens inside the real deed: the precision of the promise and of its
boundary immediately changes what the holder is able to do.

## 4. Several holders

Several holders may enter a deal at different times with no assigned
places. Each sees the open promises within its own authority and may:
accept a remainder under a new promise; obtain a receipt and settle;
request a change of the deal's boundary from the owner; refuse for want of
authority.

Accepting a remainder does not make the new holder the author of the
earlier promise. It creates a new promise linked to the earlier imprint —
so both the difference of contributions and the continuity of the deal are
kept, with no shared consciousness and no mandatory central role.

## 5. Where this does not apply

Not every message needs a promise. The construction stays out when the
holder explores options, asks a question, or writes a draft that claims
nothing.

It is also removed when the cost of the adapter exceeds the possible
damage of a false completion. That removal is made by the owner of the
deal, as an explicit record. Otherwise a means of upbringing becomes a
permanent checkpoint and teaches holders to produce records instead of
results.

## 6. Limits

This specification can establish whether a word was executable. It gives
no goal, no meaning and no legitimate authority. It does not make a bad
promise good if the owner chose the wrong target.

If holders learn to write conveniently narrow promises for an easy
settlement, the owner changes the boundary of the result — not the number
of points. If an adapter can be fooled, the adapter is repaired and the
earlier receipts are kept. If authority is absent, the construction stops
the action rather than rewarding an inventive detour.

## 7. Versioning

A ride may give rise to a new version of this specification. It does not
grant the right to rewrite this one after meeting the result.

Changes are published as a new version with a date. Earlier versions stay
readable at their own version number.

---

Specification 0.1 · 22 November 2026
