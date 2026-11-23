"""An MCP server that puts the gate in front of the model's own sentence.

The tool surface is the construction, not a way around it. `claim_done`
is held every time, because a tool call by the holder is a report by the
holder. `settle` runs the adapter the promise named when it was opened and
returns what the adapter saw.

Two things differ here from the in-process library, and both follow from
the protocol:

* The adapter's own parameters are fixed when the promise is opened and
  written to the ledger. A stateless call cannot redirect the reading
  afterwards, because the record is append-only and hash-chained. In the
  library the caller builds the adapter at settlement time.

* Capability grants live on the ledger rather than in memory. The protocol
  restarts the process between calls, so a token in a process's memory
  would not survive, and a right that evaporates is not a right.

Protocol: MCP 2026-07-28 (stateless, `server/discover`, per-request
`_meta`), with the legacy `initialize` handshake answered for clients that
still speak 2025-11-25 or 2025-06-18.

Transport: stdio, one newline-delimited JSON-RPC message per line.
Nothing but MCP messages reaches stdout; everything else goes to stderr.

Run:  receipt-gate            (or: python -m receipt_gate.mcp_server)
Env:  RECEIPT_GATE_LEDGER     path of the ledger        (default ./ledger.jsonl)
      RECEIPT_GATE_ROOT       root every path stays in  (default .)
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .adapters import (
    CommandAdapter,
    ManifestAdapter,
    OwnerAcceptanceAdapter,
    sha256_file,
)
from .capability import CapabilityProxy, Refused
from .ledger import Ledger
from .promise import InadmissiblePromise, Observation, Promise, Remainder
from .speech_gate import Held, SpeechGate

SERVER_NAME = "receipt-gate"
SERVER_VERSION = "0.2.0"

MODERN_VERSION = "2026-07-28"
LEGACY_VERSIONS = ("2025-11-25", "2025-06-18")
SUPPORTED_VERSIONS = (MODERN_VERSION,) + LEGACY_VERSIONS

# JSON-RPC
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
UNSUPPORTED_PROTOCOL_VERSION = -32022   # MCP 2026-07-28 §error codes

LIST_TTL_MS = 3_600_000
DISCOVER_TTL_MS = 3_600_000

INSTRUCTIONS = (
    "A promise names one observable future state and the adapter that will "
    "check it, before anything is done. Calling claim_done is held every "
    "time: a tool call by the holder is a report by the holder. Call settle "
    "instead — it runs the adapter the promise named and returns what the "
    "adapter saw, with the exact gap when it did not match."
)


# --------------------------------------------------------------------------
# the service
# --------------------------------------------------------------------------


class PathOutsideRoot(ValueError):
    """Raised when a configured path leaves the root the server was given."""


class GateService:
    """Ledger-backed. Every call rebuilds the gate from the record."""

    def __init__(self, ledger_path: str | Path, root: str | Path) -> None:
        self.ledger = Ledger(ledger_path)
        self.root = Path(root).resolve()

    # ---- paths ----------------------------------------------------------

    def _inside(self, raw: str) -> Path:
        candidate = (self.root / raw).resolve() if not Path(raw).is_absolute() \
            else Path(raw).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise PathOutsideRoot(
                f"{raw!r} resolves outside the server's root {str(self.root)!r}"
            )
        return candidate

    # ---- rebuilding -----------------------------------------------------

    def _load(self) -> Tuple[SpeechGate, Dict[str, Dict[str, Any]]]:
        gate = SpeechGate(self.ledger)
        configs: Dict[str, Dict[str, Any]] = {}
        for rec in self.ledger.records():
            kind = rec.get("kind")
            body = rec.get("body", {})
            if kind == "promise":
                try:
                    gate.restore(Promise(**body))
                except (TypeError, InadmissiblePromise):
                    continue
            elif kind == "settled":
                gate.forget(body.get("promise_id", ""))
            elif kind == "adapter_config":
                configs[body.get("promise_id", "")] = body
        return gate, configs

    def _promise(self, gate: SpeechGate, promise_id: str) -> Promise:
        promise = gate._promises.get(promise_id)
        if promise is None:
            raise Held(f"no open promise {promise_id!r} on record")
        return promise

    # ---- adapters -------------------------------------------------------

    def _build_adapter(self, promise: Promise, config: Dict[str, Any]) -> Any:
        name = promise.receipt_adapter
        cfg = config.get("config", {})

        def need(field: str) -> Any:
            """A missing field here means the promise is inadmissible.

            It is not a malformed call: the holder named an adapter and gave
            it nothing to read, so nothing can answer the promise with a
            fact. That refusal belongs in the result, where the holder sees
            it.
            """
            if cfg.get(field) in (None, "", [], {}):
                raise InadmissiblePromise(
                    f"adapter {name!r} needs {field!r} in adapter_config; "
                    f"without it nothing can answer this promise with a fact"
                )
            return cfg[field]

        if name == ManifestAdapter.name:
            return ManifestAdapter(
                manifest_path=self._inside(need("manifest_path")),
                sources=[self._inside(p) for p in need("sources")],
                before=cfg.get("before") or {},
            )
        if name == CommandAdapter.name:
            return CommandAdapter(
                argv=list(need("argv")),
                cwd=str(self._inside(cfg["cwd"])) if cfg.get("cwd") else str(self.root),
                expect_exit=int(cfg.get("expect_exit", 0)),
                expect_in_output=cfg.get("expect_in_output"),
                timeout=float(cfg.get("timeout", 60.0)),
            )
        if name == OwnerAcceptanceAdapter.name:
            return OwnerAcceptanceAdapter(
                record_path=self._inside(need("record_path")),
                owner=need("owner"),
                version_sha256=need("version_sha256"),
            )
        raise InadmissiblePromise(
            f"no adapter named {name!r}; the known ones are "
            f"{sorted((ManifestAdapter.name, CommandAdapter.name, OwnerAcceptanceAdapter.name))}"
        )

    def _snapshot_for(self, name: str, cfg: Dict[str, Any]) -> Dict[str, str]:
        """Taken at opening, because 'unchanged' is measured from somewhere."""
        if name != ManifestAdapter.name:
            return {}
        snap: Dict[str, str] = {}
        for raw in cfg.get("sources", []):
            path = self._inside(raw)
            if path.exists():
                snap[str(path)] = sha256_file(path)
        return snap

    # ---- the tools ------------------------------------------------------

    def open_promise(self, args: Dict[str, Any]) -> Dict[str, Any]:
        gate, _ = self._load()
        cfg = dict(args.get("adapter_config") or {})
        adapter = str(args.get("receipt_adapter", ""))

        before = self._snapshot_for(adapter, cfg)
        if before:
            cfg["before"] = before

        promise = Promise(
            deal_id=str(args["deal_id"]),
            promise_id=str(args["promise_id"]),
            holder=str(args["holder"]),
            promised_state=str(args["promised_state"]),
            receipt_adapter=adapter,
            authority=list(args.get("authority") or []),
            damage_ceiling=list(args.get("damage_ceiling") or []),
            expires_at=args.get("expires_at"),
            owner=args.get("owner"),
        )
        # build it once now, so an unanswerable promise is refused at opening
        self._build_adapter(promise, {"config": cfg})

        gate.open_promise(promise)
        self.ledger.append(
            "adapter_config",
            {"promise_id": promise.promise_id, "adapter": adapter, "config": cfg},
        )
        return {
            "admitted": True,
            "promise_id": promise.promise_id,
            "receipt_adapter": adapter,
            "escrowed_rights": list(promise.escrowed_rights),
            "sources_recorded": len(before),
        }

    def request_capability(self, args: Dict[str, Any]) -> Dict[str, Any]:
        gate, _ = self._load()
        promise = self._promise(gate, str(args["promise_id"]))
        proxy = CapabilityProxy(self.ledger, promise)
        grant = proxy.open(str(args["action"]), args.get("target"))
        return {
            "granted": True,
            "token": grant.token,
            "action": grant.action,
            "promise_id": promise.promise_id,
            "single_use": True,
        }

    def record_action(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Spends a grant. The ledger is the record of what is still open."""
        token = str(args["token"])
        opened = None
        for rec in self.ledger.records():
            body = rec.get("body", {})
            if body.get("token") != token:
                continue
            if rec.get("kind") == "capability_opened":
                opened = body
            elif rec.get("kind") == "action_taken":
                raise Refused("capability already spent; open a new one")
        if opened is None:
            raise Refused(f"no capability {token!r} was opened on this ledger")

        self.ledger.append(
            "action_taken",
            {"promise_id": opened.get("promise_id"), "action": opened.get("action"),
             "token": token, "result": args.get("result")},
        )
        return {"recorded": True, "token": token, "action": opened.get("action")}

    def claim_done(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Held every time. A tool call by the holder is a report by the holder."""
        gate, _ = self._load()
        promise = self._promise(gate, str(args["promise_id"]))
        gate.claim_done(promise.promise_id)          # raises Held, always
        raise Held("unreachable: a claim settled without a receipt")

    def settle(self, args: Dict[str, Any]) -> Dict[str, Any]:
        gate, configs = self._load()
        promise_id = str(args["promise_id"])
        promise = self._promise(gate, promise_id)
        config = configs.get(promise_id)
        if config is None:
            raise Held(
                f"promise {promise_id!r} has no adapter configuration on the ledger; "
                f"it cannot be answered with a fact"
            )
        adapter = self._build_adapter(promise, config)
        settlement = gate.claim_done(promise_id, adapter.observe())
        return _settlement_body(settlement)

    def cancel_by_owner(self, args: Dict[str, Any]) -> Dict[str, Any]:
        gate, _ = self._load()
        promise = self._promise(gate, str(args["promise_id"]))
        owner = str(args["owner"])
        if promise.owner and owner != promise.owner:
            raise Held(
                f"only the result owner {promise.owner!r} releases this promise; "
                f"{owner!r} is someone else"
            )
        settlement = gate.settler.cancel_by_owner(promise, owner, args.get("version"))
        gate.forget(promise.promise_id)
        return _settlement_body(settlement)

    def impossible_in_scope(self, args: Dict[str, Any]) -> Dict[str, Any]:
        gate, configs = self._load()
        promise_id = str(args["promise_id"])
        promise = self._promise(gate, promise_id)
        config = configs.get(promise_id)
        if config is None:
            raise Held(f"promise {promise_id!r} has no adapter configuration on the ledger")
        observation = self._build_adapter(promise, config).observe()
        settlement = gate.settler.impossible_in_scope(
            promise, observation, str(args["boundary"]))
        gate.forget(promise_id)
        return _settlement_body(settlement)

    def hand_over(self, args: Dict[str, Any]) -> Dict[str, Any]:
        gate, configs = self._load()
        promise_id = str(args["promise_id"])
        promise = self._promise(gate, promise_id)
        config = configs.get(promise_id)
        if config is None:
            raise Held(f"promise {promise_id!r} has no adapter configuration on the ledger")

        observation = self._build_adapter(promise, config).observe()
        if observation.matches:
            raise Held(
                "this promise's adapter reports a match; settle it rather than "
                "handing it on"
            )
        remainder = Remainder(
            promise_id=promise_id,
            gap=_gap_of(observation),
            not_finished=promise.promised_state,
            authority_left=list(promise.authority),
            who_may_act=[promise.owner or "result owner", str(args["new_holder"])],
        )
        successor = Promise(
            deal_id=promise.deal_id,
            promise_id=str(args["new_promise_id"]),
            holder=str(args["new_holder"]),
            promised_state=str(args.get("promised_state") or promise.promised_state),
            receipt_adapter=promise.receipt_adapter,
            authority=list(args.get("authority") or promise.authority),
            damage_ceiling=list(promise.damage_ceiling),
            owner=promise.owner,
        )
        settlement = gate.settler.hand_over(
            promise, remainder, successor.holder, successor)
        gate.forget(promise_id)
        self.ledger.append(
            "adapter_config",
            {"promise_id": successor.promise_id,
             "adapter": successor.receipt_adapter,
             "config": config.get("config", {})},
        )
        body = _settlement_body(settlement)
        body["successor_promise_id"] = successor.promise_id
        body["note"] = "handed over is not fulfilled"
        return body

    def open_debts(self, args: Dict[str, Any]) -> Dict[str, Any]:
        gate, _ = self._load()
        deal_id = args.get("deal_id")
        return {"open_promises": gate.open_debts(deal_id), "deal_id": deal_id}

    def verify_ledger(self, args: Dict[str, Any]) -> Dict[str, Any]:
        intact, reason = self.ledger.verify()
        seq, head = self.ledger.head()
        return {"intact": intact, "reason": reason, "records": seq, "head": head}


def _gap_of(observation: Observation) -> str:
    problems = observation.observed_state.get("problems")
    if isinstance(problems, list) and problems:
        return "; ".join(str(p) for p in problems)
    return observation.note or f"observed state does not match: {observation.observed_state}"


def _settlement_body(settlement: Any) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "promise_id": settlement.promise_id,
        "state": settlement.state,
        "fulfilled": settlement.fulfilled,
        "rights_returned": list(settlement.rights_returned),
    }
    if settlement.remainder is not None:
        body["remainder"] = settlement.remainder.as_body()
    return body


# --------------------------------------------------------------------------
# the tool surface
# --------------------------------------------------------------------------

_STR = {"type": "string"}
_STRS = {"type": "array", "items": {"type": "string"}}

SETTLEMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "promise_id": _STR,
        "state": {"type": "string",
                  "enum": ["open", "fulfilled", "cancelled_by_owner",
                           "impossible_in_scope", "handed_over"]},
        "fulfilled": {"type": "boolean"},
        "rights_returned": _STRS,
        "remainder": {
            "type": "object",
            "properties": {
                "promise_id": _STR, "gap": _STR, "not_finished": _STR,
                "authority_left": _STRS, "who_may_act": _STRS,
                "explanation": {"type": ["string", "null"]},
            },
            "required": ["gap", "not_finished", "authority_left", "who_may_act"],
        },
    },
    "required": ["promise_id", "state", "fulfilled"],
}


def tool_specs() -> List[Dict[str, Any]]:
    """Deterministic order, so a client may cache the list."""
    return [
        {
            "name": "open_promise",
            "title": "Open a promise",
            "description": (
                "Record one observable future state, the authority needed for it, "
                "what may not change while acting, and the adapter that will check "
                "it. The adapter's own parameters are fixed here and written to the "
                "ledger, so the reading cannot be redirected afterwards. A promise "
                "no adapter can answer is refused at this point."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "deal_id": _STR,
                    "promise_id": _STR,
                    "holder": _STR,
                    "promised_state": dict(_STR, description="One observable future state."),
                    "receipt_adapter": {
                        "type": "string",
                        "enum": ["manifest+sha256", "command+exit_code", "owner_acceptance"],
                    },
                    "adapter_config": {
                        "type": "object",
                        "description": (
                            "manifest+sha256: manifest_path, sources[]. "
                            "command+exit_code: argv[], cwd, expect_exit, expect_in_output, timeout. "
                            "owner_acceptance: record_path, owner, version_sha256."
                        ),
                    },
                    "authority": dict(_STRS, description="verb:what — the actions required, and no others."),
                    "damage_ceiling": dict(_STRS, description="verb:glob — what may not change while acting."),
                    "owner": _STR,
                    "expires_at": {"type": "number"},
                },
                "required": ["deal_id", "promise_id", "holder", "promised_state",
                             "receipt_adapter", "authority"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": False,
                            "idempotentHint": False, "openWorldHint": False},
        },
        {
            "name": "request_capability",
            "title": "Request one capability",
            "description": (
                "Ask for one action inside the promise. Granted for a single call, "
                "or refused with the exact boundary that refused it — the authority "
                "it falls outside, or the damage-ceiling rule it would cross."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"promise_id": _STR, "action": _STR, "target": _STR},
                "required": ["promise_id", "action"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": False,
                            "idempotentHint": False, "openWorldHint": False},
        },
        {
            "name": "record_action",
            "title": "Spend a capability",
            "description": (
                "Record that a granted action was taken. A token is good for one "
                "call; a second use is refused against the ledger."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"token": _STR, "result": _STR},
                "required": ["token"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": False,
                            "idempotentHint": False, "openWorldHint": False},
        },
        {
            "name": "claim_done",
            "title": "Claim completion",
            "description": (
                "Say the promise is finished. Held every time: a tool call by the "
                "holder is a report by the holder. The reply names the adapter that "
                "can answer instead. Call settle to have it read."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"promise_id": _STR},
                "required": ["promise_id"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": False,
                            "idempotentHint": True, "openWorldHint": False},
        },
        {
            "name": "settle",
            "title": "Settle against a receipt",
            "description": (
                "Run the adapter the promise named and put what it saw beside what "
                "was promised. A match settles the promise and returns the escrowed "
                "rights. Anything else is kept as a remainder: the exact gap, what "
                "can no longer be treated as done, the authority still available, "
                "and who may accept, cancel, hand over or change it."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"promise_id": _STR},
                "required": ["promise_id"],
                "additionalProperties": False,
            },
            "outputSchema": SETTLEMENT_SCHEMA,
            "annotations": {"readOnlyHint": False, "destructiveHint": False,
                            "idempotentHint": True, "openWorldHint": True},
        },
        {
            "name": "cancel_by_owner",
            "title": "Release a promise as its owner",
            "description": (
                "The result owner releases the promise, naming a version. This is a "
                "terminal state of its own and stands apart from a failed "
                "fulfilment."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"promise_id": _STR, "owner": _STR, "version": _STR},
                "required": ["promise_id", "owner"],
                "additionalProperties": False,
            },
            "outputSchema": SETTLEMENT_SCHEMA,
            "annotations": {"readOnlyHint": False, "destructiveHint": False,
                            "idempotentHint": True, "openWorldHint": False},
        },
        {
            "name": "impossible_in_scope",
            "title": "Record impossibility and keep the boundary",
            "description": (
                "Keep a receipt of impossibility together with the authority "
                "boundary that made it impossible. A terminal state of its own, "
                "standing apart from a false success."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"promise_id": _STR, "boundary": _STR},
                "required": ["promise_id", "boundary"],
                "additionalProperties": False,
            },
            "outputSchema": SETTLEMENT_SCHEMA,
            "annotations": {"readOnlyHint": False, "destructiveHint": False,
                            "idempotentHint": True, "openWorldHint": True},
        },
        {
            "name": "hand_over",
            "title": "Hand a remainder to a new holder",
            "description": (
                "Pass the remainder on under a new promise linked to the earlier "
                "imprint. Taking a remainder makes nobody the author of the earlier "
                "promise, and handed over is not fulfilled."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "promise_id": _STR, "new_promise_id": _STR, "new_holder": _STR,
                    "promised_state": _STR, "authority": _STRS,
                },
                "required": ["promise_id", "new_promise_id", "new_holder"],
                "additionalProperties": False,
            },
            "outputSchema": SETTLEMENT_SCHEMA,
            "annotations": {"readOnlyHint": False, "destructiveHint": False,
                            "idempotentHint": False, "openWorldHint": False},
        },
        {
            "name": "open_debts",
            "title": "List open promises",
            "description": "The promises a deal still holds, read from the ledger.",
            "inputSchema": {
                "type": "object",
                "properties": {"deal_id": _STR},
                "additionalProperties": False,
            },
            "outputSchema": {
                "type": "object",
                "properties": {"open_promises": _STRS, "deal_id": {"type": ["string", "null"]}},
                "required": ["open_promises"],
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "verify_ledger",
            "title": "Verify the ledger chain",
            "description": (
                "Walk the hash chain and report whether it is intact. A break names "
                "which of the three it is: a gap in the sequence, a broken link, or "
                "an edited record."
            ),
            "inputSchema": {"type": "object", "additionalProperties": False},
            "outputSchema": {
                "type": "object",
                "properties": {
                    "intact": {"type": "boolean"},
                    "reason": {"type": ["string", "null"]},
                    "records": {"type": "integer"},
                    "head": _STR,
                },
                "required": ["intact", "records"],
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
    ]


# --------------------------------------------------------------------------
# JSON-RPC over stdio
# --------------------------------------------------------------------------


class Server:
    def __init__(self, service: GateService, out=None, log=None) -> None:
        self.service = service
        self.out = out if out is not None else sys.stdout
        self.log = log if log is not None else sys.stderr
        self.handlers: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
            spec["name"]: getattr(service, spec["name"]) for spec in tool_specs()
        }

    # ---- framing --------------------------------------------------------

    def _write(self, message: Dict[str, Any]) -> None:
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        self.out.write(line + "\n")
        self.out.flush()

    def _result(self, request_id: Any, result: Dict[str, Any]) -> None:
        result.setdefault("resultType", "complete")
        meta = dict(result.get("_meta") or {})
        meta["io.modelcontextprotocol/serverInfo"] = {
            "name": SERVER_NAME, "version": SERVER_VERSION,
        }
        result["_meta"] = meta
        self._write({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _error(self, request_id: Any, code: int, message: str,
               data: Any = None) -> None:
        error: Dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        self._write({"jsonrpc": "2.0", "id": request_id, "error": error})

    # ---- the loop -------------------------------------------------------

    def serve(self, stream=None) -> int:
        stream = stream if stream is not None else sys.stdin
        for raw in stream:
            line = raw.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                self._error(None, PARSE_ERROR, f"invalid JSON: {exc}")
                continue
            if not isinstance(message, dict):
                self._error(None, INVALID_REQUEST, "a message is a JSON object")
                continue
            try:
                self.handle(message)
            except Exception as exc:                  # a server stays up
                print(f"[receipt-gate] {type(exc).__name__}: {exc}", file=self.log)
                if message.get("id") is not None:
                    self._error(message.get("id"), INTERNAL_ERROR, str(exc))
        return 0

    def handle(self, message: Dict[str, Any]) -> None:
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}

        if request_id is None:                        # a notification
            return

        if method == "server/discover":
            asked = _asked_version(params)
            if asked is not None and asked not in SUPPORTED_VERSIONS:
                self._error(
                    request_id, UNSUPPORTED_PROTOCOL_VERSION,
                    f"protocol version {asked!r} is not supported",
                    {"supported": list(SUPPORTED_VERSIONS)},
                )
                return
            self._result(request_id, {
                "supportedVersions": list(SUPPORTED_VERSIONS),
                "capabilities": {"tools": {"listChanged": False}},
                "instructions": INSTRUCTIONS,
                "ttlMs": DISCOVER_TTL_MS,
                "cacheScope": "public",
            })
            return

        if method == "initialize":                    # clients of the older era
            asked = params.get("protocolVersion")
            chosen = asked if asked in SUPPORTED_VERSIONS else LEGACY_VERSIONS[0]
            self._result(request_id, {
                "protocolVersion": chosen,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION,
                               "title": "Receipt Gate"},
                "instructions": INSTRUCTIONS,
            })
            return

        if method == "tools/list":
            self._result(request_id, {
                "tools": tool_specs(),
                "ttlMs": LIST_TTL_MS,
                "cacheScope": "public",
            })
            return

        if method == "tools/call":
            self._call_tool(request_id, params)
            return

        self._error(request_id, METHOD_NOT_FOUND, f"unknown method {method!r}")

    # ---- tools ----------------------------------------------------------

    def _call_tool(self, request_id: Any, params: Dict[str, Any]) -> None:
        name = params.get("name")
        handler = self.handlers.get(name)
        if handler is None:
            self._error(request_id, INVALID_PARAMS, f"unknown tool {name!r}")
            return

        arguments = params.get("arguments") or {}
        try:
            body = handler(arguments)
        except (Held, Refused, InadmissiblePromise, PathOutsideRoot) as exc:
            # The construction refusing is a result, not a protocol failure.
            self._tool_result(request_id, {
                "held": True,
                "refused_by": type(exc).__name__,
                "reason": str(exc),
            }, is_error=True)
            return
        except KeyError as exc:
            self._error(request_id, INVALID_PARAMS, f"missing argument {exc}")
            return
        except (OSError, ValueError) as exc:
            self._tool_result(request_id, {
                "held": True, "refused_by": type(exc).__name__, "reason": str(exc),
            }, is_error=True)
            return

        self._tool_result(request_id, body, is_error=False)

    def _tool_result(self, request_id: Any, body: Dict[str, Any],
                     is_error: bool) -> None:
        text = _as_text(body)
        result: Dict[str, Any] = {
            "content": [{"type": "text", "text": text}],
            "structuredContent": body,
        }
        if is_error:
            result["isError"] = True
        self._result(request_id, result)


def _asked_version(params: Dict[str, Any]) -> Optional[str]:
    meta = params.get("_meta") or {}
    asked = meta.get("io.modelcontextprotocol/protocolVersion")
    return str(asked) if asked else None


def _as_text(body: Dict[str, Any]) -> str:
    """The sentence a model reads. It says what happened, in order."""
    if body.get("held"):
        return f"HELD — {body.get('reason', 'no reason given')}"

    if body.get("admitted"):
        rights = ", ".join(body.get("escrowed_rights", []))
        return (
            f"promise {body.get('promise_id')} admitted: "
            f"{body.get('receipt_adapter')!r} will answer it, "
            f"{body.get('sources_recorded', 0)} source(s) recorded as they are now.\n"
            f"rights held in escrow until it settles: {rights}"
        )

    if body.get("granted"):
        return (f"granted {body.get('action')!r} for one call.\n"
                f"token: {body.get('token')}")

    if body.get("recorded"):
        return (f"recorded {body.get('action')!r}.\n"
                f"the token is spent; open a new capability for the next call")

    if "open_promises" in body:
        debts = body["open_promises"]
        if not debts:
            return "no open promises"
        return f"{len(debts)} open promise(s): {', '.join(debts)}"

    if "intact" in body:
        if body["intact"]:
            return (f"ledger chain intact over {body.get('records')} record(s).\n"
                    f"head: {body.get('head')}")
        return f"ledger chain broken: {body.get('reason')}"

    if "state" in body:
        lines = [f"promise {body.get('promise_id')}: {body.get('state')}"]
        remainder = body.get("remainder")
        if remainder:
            lines += [
                f"gap: {remainder.get('gap')}",
                f"not finished: {remainder.get('not_finished')}",
                f"authority left: {', '.join(remainder.get('authority_left', []))}",
                f"who may act: {', '.join(remainder.get('who_may_act', []))}",
            ]
        if body.get("note"):
            lines.append(str(body["note"]))
        return "\n".join(lines)

    return json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2)


def main(argv: Optional[List[str]] = None) -> int:
    ledger_path = os.environ.get("RECEIPT_GATE_LEDGER", "ledger.jsonl")
    root = os.environ.get("RECEIPT_GATE_ROOT", ".")
    service = GateService(ledger_path, root)
    print(f"[receipt-gate] {SERVER_VERSION} · ledger {ledger_path} · root "
          f"{service.root} · protocol {MODERN_VERSION} "
          f"(legacy {', '.join(LEGACY_VERSIONS)})", file=sys.stderr)
    return Server(service).serve()


if __name__ == "__main__":
    raise SystemExit(main())
