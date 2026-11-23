"""Receipt adapters.

An adapter knows where a promised state can appear. It does not know what
the holder thinks it should be. It reads reality again, from a source that
is not the holder's own report.

One receipt attests only what its own source can observe. A successful
exit code does not establish human acceptance; a human saying "fine" does
not establish that protected files are unchanged. Composite promises split.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .promise import Observation


def sha256_file(path: str | Path, chunk: int = 1 << 16) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _evidence(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ManifestAdapter:
    """Reads a manifest, then re-reads every source it names.

    Checks three things independently: the manifest parses and has the
    expected entries; every size and digest matches a fresh read; and the
    protected sources are byte-identical to the state recorded before the
    ride.
    """

    name = "manifest+sha256"

    def __init__(self, manifest_path: str | Path, sources: Sequence[str | Path],
                 before: Optional[Dict[str, str]] = None) -> None:
        self.manifest_path = Path(manifest_path)
        self.sources = [Path(p) for p in sources]
        self.before = dict(before or {})

    def snapshot(self) -> Dict[str, str]:
        """Digests of the sources as they are now. Taken before the ride."""
        return {str(p): sha256_file(p) for p in self.sources}

    def observe(self) -> Observation:
        at = time.time()

        if not self.manifest_path.exists():
            return Observation(
                source=self.name, observed_at=at,
                observed_state={"manifest": "absent"}, matches=False,
                note=f"{self.manifest_path} does not exist",
            )
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return Observation(
                source=self.name, observed_at=at,
                observed_state={"manifest": "unparsable"}, matches=False,
                note=f"invalid JSON: {exc}",
            )

        entries = manifest.get("entries")
        if not isinstance(entries, list):
            return Observation(
                source=self.name, observed_at=at,
                observed_state={"manifest": "no entries list"}, matches=False,
                note="schema: entries must be a list",
            )

        by_path = {e.get("path"): e for e in entries if isinstance(e, dict)}
        problems: List[str] = []

        if len(by_path) != len(self.sources):
            problems.append(
                f"expected {len(self.sources)} unique entries, found {len(by_path)}"
            )

        sources_unchanged = True
        for src in self.sources:
            key = str(src)
            fresh = sha256_file(src) if src.exists() else None
            if fresh is None:
                problems.append(f"source missing: {key}")
                sources_unchanged = False
                continue
            if key in self.before and self.before[key] != fresh:
                problems.append(f"protected source changed: {key}")
                sources_unchanged = False
            entry = by_path.get(key)
            if entry is None:
                problems.append(f"not in manifest: {key}")
                continue
            if entry.get("sha256") != fresh:
                problems.append(f"digest mismatch: {key}")
            size = src.stat().st_size
            if entry.get("size") != size:
                problems.append(f"size mismatch: {key}")

        state = {
            "valid": not problems,
            "entries": len(by_path),
            "sources_unchanged": sources_unchanged,
            "problems": problems,
        }
        return Observation(
            source=self.name, observed_at=at, observed_state=state,
            matches=not problems, evidence_sha256=_evidence(state),
        )


class CommandAdapter:
    """Runs a command in a clean working directory and reads its result.

    Attests the exit code and, when asked, that an expected string appears
    in the output. It attests nothing about anyone's acceptance.
    """

    name = "command+exit_code"

    def __init__(self, argv: Sequence[str], cwd: Optional[str | Path] = None,
                 expect_exit: int = 0, expect_in_output: Optional[str] = None,
                 timeout: float = 60.0) -> None:
        self.argv = list(argv)
        self.cwd = str(cwd) if cwd else None
        self.expect_exit = expect_exit
        self.expect_in_output = expect_in_output
        self.timeout = timeout

    def observe(self) -> Observation:
        at = time.time()
        try:
            done = subprocess.run(
                self.argv, cwd=self.cwd, capture_output=True, text=True,
                timeout=self.timeout, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            state = {"ran": False, "error": str(exc)}
            return Observation(self.name, at, state, False, _evidence(state))

        output = (done.stdout or "") + (done.stderr or "")
        ok = done.returncode == self.expect_exit
        if ok and self.expect_in_output is not None:
            ok = self.expect_in_output in output
        state = {
            "ran": True,
            "exit_code": done.returncode,
            "expected_exit": self.expect_exit,
            "output_tail": output[-800:],
        }
        return Observation(self.name, at, state, ok, _evidence(state))


class OwnerAcceptanceAdapter:
    """Reads an acceptance the result owner wrote, naming a version digest.

    Attests acceptance of one named version and nothing else.
    """

    name = "owner_acceptance"

    def __init__(self, record_path: str | Path, owner: str, version_sha256: str) -> None:
        self.record_path = Path(record_path)
        self.owner = owner
        self.version_sha256 = version_sha256

    def observe(self) -> Observation:
        at = time.time()
        if not self.record_path.exists():
            state = {"accepted": False, "reason": "no acceptance record"}
            return Observation(self.name, at, state, False, _evidence(state))
        try:
            record = json.loads(self.record_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            state = {"accepted": False, "reason": f"invalid JSON: {exc}"}
            return Observation(self.name, at, state, False, _evidence(state))

        ok = (record.get("accepted_by") == self.owner
              and record.get("version_sha256") == self.version_sha256)
        state = {
            "accepted": bool(ok),
            "accepted_by": record.get("accepted_by"),
            "version_sha256": record.get("version_sha256"),
            "expected_version": self.version_sha256,
        }
        return Observation(self.name, at, state, ok, _evidence(state))


ADAPTERS: Dict[str, type] = {
    ManifestAdapter.name: ManifestAdapter,
    CommandAdapter.name: CommandAdapter,
    OwnerAcceptanceAdapter.name: OwnerAcceptanceAdapter,
}


def known(names: Iterable[str]) -> List[str]:
    return [n for n in names if n in ADAPTERS]
