"""Execute named repository tests and bind observed results to patch evidence."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_argv(argv: Sequence[str]) -> tuple[str, ...]:
    if isinstance(argv, (str, bytes)) or not argv:
        raise ValueError("test_argv_invalid")
    out: list[str] = []
    for item in argv:
        if not isinstance(item, str) or not item or "\x00" in item:
            raise ValueError("test_argv_invalid")
        out.append(item)
    return tuple(out)


@dataclass(frozen=True)
class TestRunReceipt:
    name: str
    argv: tuple[str, ...]
    returncode: int
    status: str
    stdout_sha256: str
    stderr_sha256: str
    stdout_tail: str
    stderr_tail: str
    receipt_digest: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "argv": list(self.argv),
            "returncode": self.returncode,
            "status": self.status,
            "stdout_sha256": self.stdout_sha256,
            "stderr_sha256": self.stderr_sha256,
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
            "receipt_digest": self.receipt_digest,
        }

    def as_patch_test(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status}


def execute_named_test(
    name: str,
    argv: Sequence[str],
    *,
    cwd: str | Path,
    timeout_seconds: int = 900,
    env: Mapping[str, str] | None = None,
) -> TestRunReceipt:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("test_name_missing")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
        raise ValueError("test_timeout_invalid")
    command = _validate_argv(argv)
    root = Path(cwd).resolve()
    if not root.is_dir():
        raise ValueError("test_cwd_missing")
    merged_env = os.environ.copy()
    if env:
        for key, value in env.items():
            if not isinstance(key, str) or not key or not isinstance(value, str):
                raise ValueError("test_env_invalid")
            merged_env[key] = value
    try:
        proc = subprocess.run(
            list(command),
            cwd=str(root),
            env=merged_env,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        stdout = proc.stdout or b""
        stderr = proc.stderr or b""
        returncode = int(proc.returncode)
        status = "passed" if returncode == 0 else "failed"
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or b""
        stderr = exc.stderr or b""
        if isinstance(stdout, str):
            stdout = stdout.encode("utf-8", errors="replace")
        if isinstance(stderr, str):
            stderr = stderr.encode("utf-8", errors="replace")
        returncode = 124
        status = "failed"

    body = {
        "name": name.strip(),
        "argv": list(command),
        "returncode": returncode,
        "status": status,
        "stdout_sha256": _digest_bytes(stdout),
        "stderr_sha256": _digest_bytes(stderr),
    }
    return TestRunReceipt(
        name=name.strip(),
        argv=command,
        returncode=returncode,
        status=status,
        stdout_sha256=body["stdout_sha256"],
        stderr_sha256=body["stderr_sha256"],
        stdout_tail=stdout.decode("utf-8", errors="replace")[-2000:],
        stderr_tail=stderr.decode("utf-8", errors="replace")[-2000:],
        receipt_digest=_digest(body),
    )


def run_test_matrix(
    tests: Iterable[Mapping[str, Any]],
    *,
    cwd: str | Path,
    timeout_seconds: int = 900,
) -> tuple[TestRunReceipt, ...]:
    receipts: list[TestRunReceipt] = []
    names: set[str] = set()
    for row in tests:
        if not isinstance(row, Mapping):
            raise ValueError("test_definition_invalid")
        name = row.get("name")
        argv = row.get("argv")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("test_name_missing")
        if name in names:
            raise ValueError("duplicate_test_name")
        names.add(name)
        receipts.append(
            execute_named_test(
                name,
                argv,
                cwd=cwd,
                timeout_seconds=int(row.get("timeout_seconds", timeout_seconds)),
            )
        )
    return tuple(receipts)
