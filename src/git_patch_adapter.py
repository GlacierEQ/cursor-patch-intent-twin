"""Derive Patch Intent Twin observations from a real git repository.

The adapter never trusts caller-supplied changed-file facts. It resolves exact
commit SHAs, reads Git's rename-aware name-status and numstat output, extracts
symbols from exact head blobs, records dependency-manifest additions, and emits
a deterministic provenance digest over the observed patch.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class GitObservationError(ValueError):
    pass


def _digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-c", "color.ui=false", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    if check and proc.returncode != 0:
        reason = (proc.stderr or proc.stdout or "git command failed").strip()
        raise GitObservationError(reason[:1000])
    return proc.stdout


def _resolve(repo: Path, ref: str) -> str:
    if not isinstance(ref, str) or not ref.strip():
        raise GitObservationError("git_ref_missing")
    sha = _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise GitObservationError("resolved_ref_not_commit")
    return sha


def _symbols(path: str, text: str) -> list[str]:
    suffix = Path(path).suffix.lower()
    patterns: list[str]
    if suffix == ".py":
        patterns = [r"(?m)^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)", r"(?m)^\s*class\s+([A-Za-z_]\w*)"]
    elif suffix in {".ts", ".tsx", ".js", ".jsx"}:
        patterns = [
            r"(?m)^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)",
            r"(?m)^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)",
            r"(?m)^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=",
        ]
    elif suffix == ".go":
        patterns = [r"(?m)^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)", r"(?m)^type\s+([A-Za-z_]\w*)\s+"]
    elif suffix == ".rs":
        patterns = [r"(?m)^\s*(?:pub\s+)?fn\s+([A-Za-z_]\w*)", r"(?m)^\s*(?:pub\s+)?(?:struct|enum|trait)\s+([A-Za-z_]\w*)"]
    else:
        return []
    found: set[str] = set()
    for pattern in patterns:
        found.update(re.findall(pattern, text))
    return sorted(found)


def _blob_text(repo: Path, commit: str, path: str) -> str:
    proc = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        cwd=str(repo),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return ""
    try:
        return proc.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def _name_status(repo: Path, base: str, head: str) -> list[tuple[str, str, str | None]]:
    output = _git(repo, "diff", "--name-status", "-M", base, head)
    rows: list[tuple[str, str, str | None]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        code = parts[0]
        kind = code[0]
        if kind == "R":
            if len(parts) != 3:
                raise GitObservationError("rename_row_malformed")
            rows.append(("renamed", parts[2], parts[1]))
        else:
            if len(parts) != 2:
                raise GitObservationError("name_status_row_malformed")
            status = {"A": "added", "M": "modified", "D": "deleted", "T": "modified"}.get(kind)
            if status is None:
                raise GitObservationError(f"unsupported_git_status:{code}")
            rows.append((status, parts[1], None))
    return rows


def _numstat(repo: Path, base: str, head: str) -> dict[str, tuple[int, int]]:
    output = _git(repo, "diff", "--numstat", "-M", base, head)
    stats: dict[str, tuple[int, int]] = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            raise GitObservationError("numstat_row_malformed")
        adds_raw, dels_raw = parts[0], parts[1]
        path = parts[-1]
        if " => " in path and "{" not in path:
            path = path.split(" => ", 1)[-1]
        elif "{" in path and " => " in path:
            prefix, rest = path.split("{", 1)
            middle, suffix = rest.split("}", 1)
            path = prefix + middle.split(" => ", 1)[-1] + suffix
        additions = 0 if adds_raw == "-" else int(adds_raw)
        deletions = 0 if dels_raw == "-" else int(dels_raw)
        stats[path] = (additions, deletions)
    return stats


def _dependency_additions(repo: Path, base: str, head: str, changed_paths: list[str]) -> list[str]:
    manifests = {
        "requirements.txt", "pyproject.toml", "package.json", "Cargo.toml", "go.mod"
    }
    additions: set[str] = set()
    for path in changed_paths:
        if Path(path).name not in manifests:
            continue
        diff = _git(repo, "diff", "--unified=0", base, head, "--", path, check=False)
        for line in diff.splitlines():
            if not line.startswith("+") or line.startswith("+++"):
                continue
            value = line[1:].strip()
            if not value or value.startswith(("#", "//", "}")):
                continue
            additions.add(f"{path}:{value[:240]}")
    return sorted(additions)


@dataclass(frozen=True)
class GitPatchObservation:
    base_sha: str
    head_sha: str
    changed_files: tuple[dict[str, Any], ...]
    dependencies_added: tuple[str, ...]
    observation_digest: str

    def as_patch(self) -> dict[str, Any]:
        return {
            "changed_files": [dict(row) for row in self.changed_files],
            "dependencies_added": list(self.dependencies_added),
            "tests": [],
            "provenance": {
                "base_sha": self.base_sha,
                "head_sha": self.head_sha,
                "observation_digest": self.observation_digest,
                "source": "git",
            },
        }


def observe_git_patch(repo: str | Path, base_ref: str, head_ref: str = "HEAD") -> GitPatchObservation:
    root = Path(repo).resolve()
    if not (root / ".git").exists():
        raise GitObservationError("repository_not_git_worktree")
    base_sha = _resolve(root, base_ref)
    head_sha = _resolve(root, head_ref)
    if base_sha == head_sha:
        raise GitObservationError("empty_commit_range")

    statuses = _name_status(root, base_sha, head_sha)
    stats = _numstat(root, base_sha, head_sha)
    changed: list[dict[str, Any]] = []
    for status, path, old_path in statuses:
        additions, deletions = stats.get(path, (0, 0))
        text = "" if status == "deleted" else _blob_text(root, head_sha, path)
        row: dict[str, Any] = {
            "path": path,
            "status": status,
            "additions": additions,
            "deletions": deletions,
            "symbols": _symbols(path, text),
        }
        if old_path:
            row["old_path"] = old_path
        changed.append(row)
    changed.sort(key=lambda row: row["path"])
    dependencies = _dependency_additions(root, base_sha, head_sha, [row["path"] for row in changed])
    body = {
        "base_sha": base_sha,
        "head_sha": head_sha,
        "changed_files": changed,
        "dependencies_added": dependencies,
    }
    return GitPatchObservation(
        base_sha=base_sha,
        head_sha=head_sha,
        changed_files=tuple(changed),
        dependencies_added=tuple(dependencies),
        observation_digest=_digest(body),
    )
