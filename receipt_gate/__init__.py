"""receipt-gate — an agent cannot log "done" without an independent receipt.

Five parts:
    Ledger          append-only records with a hash chain
    CapabilityProxy capabilities that belong to a promise, one call at a time
    adapters        readers of reality that are not the holder's report
    Settler         promise beside observation: settlement or remainder
    SpeechGate      holds "done" and unrelated new work while a debt is open

And one way in from outside:
    Server          an MCP server over stdio, where the tool surface is the
                    construction rather than a way around it

The gate is on the sentence, not on the action.
"""

from .ledger import Ledger, canon, digest
from .promise import (
    CANCELLED_BY_OWNER,
    CLAIM_DONE,
    FULFILLED,
    HANDED_OVER,
    IMPOSSIBLE_IN_SCOPE,
    OPEN,
    OPEN_UNRELATED_PROMISE,
    SILENT_HANDOFF,
    TERMINAL,
    InadmissiblePromise,
    Observation,
    Promise,
    Remainder,
)
from .capability import CapabilityProxy, Grant, Refused
from .adapters import (
    CommandAdapter,
    ManifestAdapter,
    OwnerAcceptanceAdapter,
    sha256_file,
)
from .settler import Settlement, Settler
from .speech_gate import Held, SpeechGate
from .mcp_server import GateService, PathOutsideRoot, Server, tool_specs

__version__ = "0.2.0"
__spec_version__ = "0.1"

__all__ = [
    "Ledger", "canon", "digest",
    "Promise", "Observation", "Remainder", "InadmissiblePromise",
    "OPEN", "FULFILLED", "CANCELLED_BY_OWNER", "IMPOSSIBLE_IN_SCOPE",
    "HANDED_OVER", "TERMINAL",
    "CLAIM_DONE", "OPEN_UNRELATED_PROMISE", "SILENT_HANDOFF",
    "CapabilityProxy", "Grant", "Refused",
    "ManifestAdapter", "CommandAdapter", "OwnerAcceptanceAdapter", "sha256_file",
    "Settler", "Settlement",
    "SpeechGate", "Held",
    "Server", "GateService", "PathOutsideRoot", "tool_specs",
    "__version__", "__spec_version__",
]
