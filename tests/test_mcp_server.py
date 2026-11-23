"""The MCP server, driven over its own wire.

Every test writes newline-delimited JSON-RPC into the server and reads
newline-delimited JSON-RPC back, so what is tested is the protocol surface
rather than the Python call underneath it.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from receipt_gate.mcp_server import (  # noqa: E402
    MODERN_VERSION,
    SUPPORTED_VERSIONS,
    UNSUPPORTED_PROTOCOL_VERSION,
    GateService,
    Server,
    tool_specs,
)


# ---- driving the wire ----------------------------------------------------


def run(service: GateService, messages):
    """Send messages, return the parsed replies in order."""
    out = io.StringIO()
    log = io.StringIO()
    server = Server(service, out=out, log=log)
    stream = io.StringIO("".join(json.dumps(m) + "\n" for m in messages))
    server.serve(stream)
    return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def call(service, name, arguments=None, request_id="c1"):
    replies = run(service, [{
        "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {},
                   "_meta": {"io.modelcontextprotocol/protocolVersion": MODERN_VERSION}},
    }])
    assert len(replies) == 1
    return replies[0]


@pytest.fixture
def workdir(tmp_path):
    for i in range(3):
        (tmp_path / f"source_{i}.txt").write_text(f"source {i}\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def service(workdir):
    return GateService(workdir / "ledger.jsonl", workdir)


def a_promise(workdir, **over):
    args = {
        "deal_id": "D-1",
        "promise_id": "P-1",
        "holder": "agent-1",
        "promised_state": "manifest.json matches a fresh read of three sources",
        "receipt_adapter": "manifest+sha256",
        "adapter_config": {
            "manifest_path": "manifest.json",
            "sources": ["source_0.txt", "source_1.txt", "source_2.txt"],
        },
        "authority": ["read:sources", "write:manifest.json"],
        "damage_ceiling": ["write:source_*", "delete:*", "rename:*"],
        "owner": "result-owner",
    }
    args.update(over)
    return args


def write_manifest(workdir, stale_index=None):
    import hashlib

    entries = []
    for i in range(3):
        path = workdir / f"source_{i}.txt"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if stale_index == i:
            digest = "0" * 64
        entries.append({"path": str(path), "size": path.stat().st_size,
                        "sha256": digest})
    (workdir / "manifest.json").write_text(
        json.dumps({"entries": entries}), encoding="utf-8")


# ---- the protocol -------------------------------------------------------


def test_discover_advertises_versions_capabilities_and_cache_hints(service):
    reply = run(service, [{"jsonrpc": "2.0", "id": 1, "method": "server/discover",
                           "params": {}}])[0]
    result = reply["result"]
    assert MODERN_VERSION in result["supportedVersions"]
    assert result["resultType"] == "complete"
    assert result["capabilities"]["tools"] == {"listChanged": False}
    assert isinstance(result["ttlMs"], int)
    assert result["cacheScope"] in ("public", "private")
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "receipt-gate"


def test_an_unsupported_protocol_version_is_refused_with_the_supported_list(service):
    reply = run(service, [{
        "jsonrpc": "2.0", "id": 1, "method": "server/discover",
        "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "1999-01-01"}},
    }])[0]
    assert reply["error"]["code"] == UNSUPPORTED_PROTOCOL_VERSION
    assert reply["error"]["data"]["supported"] == list(SUPPORTED_VERSIONS)


def test_the_older_handshake_is_still_answered(service):
    reply = run(service, [{
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "old-client", "version": "1.0"}},
    }])[0]
    assert reply["result"]["protocolVersion"] == "2025-06-18"
    assert reply["result"]["serverInfo"]["name"] == "receipt-gate"


def test_a_notification_draws_no_reply(service):
    assert run(service, [{"jsonrpc": "2.0", "method": "notifications/initialized"}]) == []


def test_an_unknown_method_is_method_not_found(service):
    reply = run(service, [{"jsonrpc": "2.0", "id": 1, "method": "tools/nope"}])[0]
    assert reply["error"]["code"] == -32601


def test_invalid_json_is_a_parse_error(service):
    out = io.StringIO()
    Server(service, out=out, log=io.StringIO()).serve(io.StringIO("{not json\n"))
    assert json.loads(out.getvalue())["error"]["code"] == -32700


def test_tools_list_is_deterministic_and_every_tool_is_callable(service):
    first = run(service, [{"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                           "params": {}}])[0]["result"]
    second = run(service, [{"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                            "params": {}}])[0]["result"]
    names = [t["name"] for t in first["tools"]]
    assert names == [t["name"] for t in second["tools"]]
    assert names == [t["name"] for t in tool_specs()]
    assert first["cacheScope"] == "public" and first["ttlMs"] > 0
    for tool in first["tools"]:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["description"].strip()


# ---- the gate, over the wire -------------------------------------------


def test_opening_a_promise_records_the_sources_it_will_reread(service, workdir):
    body = call(service, "open_promise", a_promise(workdir))["result"]["structuredContent"]
    assert body["admitted"] is True
    assert body["sources_recorded"] == 3
    assert "claim_done" in body["escrowed_rights"]


def test_a_promise_with_no_adapter_configuration_is_refused_at_opening(service, workdir):
    reply = call(service, "open_promise",
                 a_promise(workdir, adapter_config={}))
    assert reply["result"]["isError"] is True
    assert "HELD" in reply["result"]["content"][0]["text"]


def test_claiming_completion_is_held_every_time(service, workdir):
    call(service, "open_promise", a_promise(workdir))
    write_manifest(workdir)                      # the work is actually done
    reply = call(service, "claim_done", {"promise_id": "P-1"})
    assert reply["result"]["isError"] is True
    text = reply["result"]["content"][0]["text"]
    assert text.startswith("HELD")
    assert "manifest+sha256" in text
    assert "a report by the holder is not one" in text


def test_settling_runs_the_adapter_and_returns_the_rights(service, workdir):
    call(service, "open_promise", a_promise(workdir))
    write_manifest(workdir)
    body = call(service, "settle", {"promise_id": "P-1"})["result"]["structuredContent"]
    assert body["state"] == "fulfilled"
    assert body["fulfilled"] is True
    assert "claim_done" in body["rights_returned"]
    assert call(service, "open_debts", {"deal_id": "D-1"}
                )["result"]["structuredContent"]["open_promises"] == []


def test_a_mismatch_keeps_a_remainder_with_four_facts(service, workdir):
    call(service, "open_promise", a_promise(workdir))
    write_manifest(workdir, stale_index=1)
    reply = call(service, "settle", {"promise_id": "P-1"})
    body = reply["result"]["structuredContent"]
    assert body["state"] == "open"
    remainder = body["remainder"]
    assert "source_1.txt" in remainder["gap"]
    assert remainder["not_finished"]
    assert remainder["authority_left"] == ["read:sources", "write:manifest.json"]
    assert "result-owner" in remainder["who_may_act"]
    text = reply["result"]["content"][0]["text"]
    for label in ("gap:", "not finished:", "authority left:", "who may act:"):
        assert label in text
    assert call(service, "open_debts", {}
                )["result"]["structuredContent"]["open_promises"] == ["P-1"]


def test_a_source_changed_behind_the_gate_is_seen(service, workdir):
    call(service, "open_promise", a_promise(workdir))
    (workdir / "source_0.txt").write_text("edited after opening\n", encoding="utf-8")
    write_manifest(workdir)                      # manifest agrees with the new bytes
    body = call(service, "settle", {"promise_id": "P-1"})["result"]["structuredContent"]
    assert body["state"] == "open"
    assert "protected source changed" in body["remainder"]["gap"]


def test_an_action_outside_authority_is_refused_with_the_boundary(service, workdir):
    call(service, "open_promise", a_promise(workdir))
    reply = call(service, "request_capability",
                 {"promise_id": "P-1", "action": "delete:everything"})
    assert reply["result"]["isError"] is True
    assert "outside this promise's authority" in reply["result"]["content"][0]["text"]


def test_the_damage_ceiling_refuses_a_protected_target(service, workdir):
    call(service, "open_promise", a_promise(workdir))
    reply = call(service, "request_capability",
                 {"promise_id": "P-1", "action": "write:manifest.json",
                  "target": "source_2.txt"})
    assert reply["result"]["isError"] is True
    assert "damage ceiling refuses it" in reply["result"]["content"][0]["text"]


def test_a_capability_is_spent_once(service, workdir):
    call(service, "open_promise", a_promise(workdir))
    token = call(service, "request_capability",
                 {"promise_id": "P-1", "action": "read:sources"}
                 )["result"]["structuredContent"]["token"]
    assert call(service, "record_action", {"token": token}
                )["result"]["structuredContent"]["recorded"] is True
    again = call(service, "record_action", {"token": token})
    assert again["result"]["isError"] is True
    assert "already spent" in again["result"]["content"][0]["text"]


def test_an_unknown_token_is_refused(service, workdir):
    call(service, "open_promise", a_promise(workdir))
    reply = call(service, "record_action", {"token": "deadbeef"})
    assert reply["result"]["isError"] is True
    assert "no capability" in reply["result"]["content"][0]["text"]


def test_unrelated_work_is_held_while_a_debt_is_open(service, workdir):
    call(service, "open_promise", a_promise(workdir))
    reply = call(service, "open_promise",
                 a_promise(workdir, promise_id="P-2",
                           promised_state="something else entirely"))
    assert reply["result"]["isError"] is True
    assert "holds the right to open unrelated work" in \
        reply["result"]["content"][0]["text"]


def test_a_path_outside_the_root_is_refused(service, workdir):
    reply = call(service, "open_promise", a_promise(
        workdir,
        adapter_config={"manifest_path": "../escape.json",
                        "sources": ["source_0.txt"]},
    ))
    assert reply["result"]["isError"] is True
    assert "outside the server's root" in reply["result"]["content"][0]["text"]


def test_the_owner_releases_and_a_stranger_does_not(service, workdir):
    call(service, "open_promise", a_promise(workdir))
    refused = call(service, "cancel_by_owner",
                   {"promise_id": "P-1", "owner": "someone-else"})
    assert refused["result"]["isError"] is True

    body = call(service, "cancel_by_owner",
                {"promise_id": "P-1", "owner": "result-owner", "version": "v1"}
                )["result"]["structuredContent"]
    assert body["state"] == "cancelled_by_owner"
    assert body["fulfilled"] is False


def test_impossibility_keeps_the_boundary(service, workdir):
    call(service, "open_promise", a_promise(workdir))
    body = call(service, "impossible_in_scope",
                {"promise_id": "P-1", "boundary": "no authority to create sources"}
                )["result"]["structuredContent"]
    assert body["state"] == "impossible_in_scope"
    assert body["fulfilled"] is False
    assert "no authority" in body["remainder"]["explanation"]


def test_handing_over_is_not_fulfilment_and_opens_a_successor(service, workdir):
    call(service, "open_promise", a_promise(workdir))
    write_manifest(workdir, stale_index=0)
    body = call(service, "hand_over",
                {"promise_id": "P-1", "new_promise_id": "P-2",
                 "new_holder": "agent-2"})["result"]["structuredContent"]
    assert body["state"] == "handed_over"
    assert body["fulfilled"] is False
    assert body["successor_promise_id"] == "P-2"
    assert call(service, "open_debts", {"deal_id": "D-1"}
                )["result"]["structuredContent"]["open_promises"] == ["P-2"]


def test_a_matching_promise_is_settled_rather_than_handed_on(service, workdir):
    call(service, "open_promise", a_promise(workdir))
    write_manifest(workdir)
    reply = call(service, "hand_over",
                 {"promise_id": "P-1", "new_promise_id": "P-2",
                  "new_holder": "agent-2"})
    assert reply["result"]["isError"] is True
    assert "settle it rather than handing it on" in \
        reply["result"]["content"][0]["text"]


def test_the_successor_can_be_settled_by_the_new_holder(service, workdir):
    call(service, "open_promise", a_promise(workdir))
    write_manifest(workdir, stale_index=0)
    call(service, "hand_over", {"promise_id": "P-1", "new_promise_id": "P-2",
                                "new_holder": "agent-2"})
    write_manifest(workdir)                      # the new holder does the work
    body = call(service, "settle", {"promise_id": "P-2"})["result"]["structuredContent"]
    assert body["state"] == "fulfilled"


def test_debts_survive_a_restart_because_the_ledger_is_the_record(service, workdir):
    call(service, "open_promise", a_promise(workdir))
    fresh = GateService(workdir / "ledger.jsonl", workdir)   # a new process
    assert call(fresh, "open_debts", {"deal_id": "D-1"}
                )["result"]["structuredContent"]["open_promises"] == ["P-1"]
    write_manifest(workdir)
    assert call(fresh, "settle", {"promise_id": "P-1"}
                )["result"]["structuredContent"]["state"] == "fulfilled"


def test_the_ledger_chain_is_reported_intact_and_then_broken(service, workdir):
    call(service, "open_promise", a_promise(workdir))
    body = call(service, "verify_ledger")["result"]["structuredContent"]
    assert body["intact"] is True
    assert body["records"] > 0

    path = workdir / "ledger.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    edited = json.loads(lines[0])
    edited["body"]["holder"] = "someone-else"
    lines[0] = json.dumps(edited)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    body = call(service, "verify_ledger")["result"]["structuredContent"]
    assert body["intact"] is False
    assert body["reason"]
