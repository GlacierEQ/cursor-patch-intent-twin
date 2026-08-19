"""Recover Patch Intent Twin material as continuation-oriented evidence.

The resolver retains every parseable contract value and its provenance. Evidence
gaps, contradictions, and malformed field values become explicit resolution work
rather than exceptions that erase the partial intent already recovered.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable


_LIST_FIELDS = {
    "required paths": "required_paths",
    "allowed paths": "allowed_paths",
    "forbidden paths": "forbidden_paths",
    "required tests": "required_tests",
    "forbidden dependencies": "forbidden_dependencies",
}
_INT_FIELDS = {"max files changed": "max_files_changed", "max deletions": "max_deletions"}
_BOOL_FIELDS = {"allow file deletion": "allow_file_deletion"}
_SYMBOL_RE = re.compile(r"^required symbols?\s+(.+?)\s*:\s*(.+)$", re.I)
_KEY_VALUE_RE = re.compile(r"^\s*(?:[-*]\s*)?([^:]+?)\s*:\s*(.*?)\s*$")


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False).encode("utf-8")).hexdigest()


def _split_values(raw: str) -> tuple[list[str], str | None]:
    value = raw.strip()
    if value.startswith("["):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [], "clarify_list_syntax"
        if not isinstance(parsed, list) or not all(isinstance(item, str) and item.strip() for item in parsed):
            return [], "clarify_list_values"
        return [item.strip() for item in parsed], None
    values = [item.strip() for item in re.split(r"\s*,\s*|\s*;\s*", value) if item.strip()]
    return values, None if values else "supply_list_values"


def _parse_bool(raw: str) -> tuple[bool | None, str | None]:
    value = raw.strip().lower()
    if value in {"true", "yes", "allow", "allowed"}:
        return True, None
    if value in {"false", "no", "deny", "denied", "forbid", "forbidden"}:
        return False, None
    return None, "clarify_boolean_value"


@dataclass(frozen=True)
class IntentEvidence:
    source_id: str
    source_uri: str
    text: str

    def issues(self) -> tuple[str, ...]:
        issues: list[str] = []
        if not isinstance(self.source_id, str) or not self.source_id.strip():
            issues.append("complete_source_id")
        if not isinstance(self.source_uri, str) or not self.source_uri.strip():
            issues.append("complete_source_uri")
        if not isinstance(self.text, str) or not self.text.strip():
            issues.append("supply_source_text")
        return tuple(issues)


@dataclass(frozen=True)
class RecoveredIntent:
    contract: dict[str, Any]
    provenance: tuple[dict[str, Any], ...]
    source_digest: str
    contract_digest: str
    continuation: str
    resolution_work: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract": self.contract,
            "provenance": [dict(row) for row in self.provenance],
            "source_digest": self.source_digest,
            "contract_digest": self.contract_digest,
            "continuation": self.continuation,
            "resolution_work": list(self.resolution_work),
        }


def recover_intent(evidence: Iterable[IntentEvidence]) -> RecoveredIntent:
    """Recover all observable intent material and return the next resolution steps."""
    sources = list(evidence)
    work: list[str] = []
    if not sources:
        work.append("add_intent_evidence")
    list_values: dict[str, set[str]] = {field: set() for field in _LIST_FIELDS.values()}
    int_values: dict[str, list[int]] = {field: [] for field in _INT_FIELDS.values()}
    bool_values: dict[str, list[bool]] = {field: [] for field in _BOOL_FIELDS.values()}
    symbols: dict[str, set[str]] = {}
    provenance: list[dict[str, Any]] = []

    for source_index, source in enumerate(sources):
        if not isinstance(source, IntentEvidence):
            work.append(f"normalize_evidence_row:{source_index}")
            continue
        source_issues = source.issues()
        work.extend(f"source:{source_index}:{issue}" for issue in source_issues)
        if source_issues:
            continue
        for line_number, line in enumerate(source.text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            symbol_match = _SYMBOL_RE.match(stripped.lstrip("-* "))
            if symbol_match:
                path = symbol_match.group(1).strip().strip("`")
                values, issue = _split_values(symbol_match.group(2))
                if not path:
                    work.append(f"line:{source.source_id}:{line_number}:complete_required_symbol_path")
                elif issue:
                    work.append(f"line:{source.source_id}:{line_number}:{issue}")
                else:
                    symbols.setdefault(path, set()).update(values)
                    provenance.append({"field": f"required_symbols.{path}", "values": values, "source_id": source.source_id, "source_uri": source.source_uri, "line": line_number, "line_digest": hashlib.sha256(line.encode()).hexdigest()})
                continue
            match = _KEY_VALUE_RE.match(stripped)
            if not match:
                continue
            key = re.sub(r"\s+", " ", match.group(1).strip().lower().strip("`"))
            raw = match.group(2).strip()
            if key in _LIST_FIELDS:
                field = _LIST_FIELDS[key]
                values, issue = _split_values(raw)
                if issue:
                    work.append(f"line:{source.source_id}:{line_number}:{field}:{issue}")
                else:
                    list_values[field].update(values)
                    provenance.append({"field": field, "values": values, "source_id": source.source_id, "source_uri": source.source_uri, "line": line_number, "line_digest": hashlib.sha256(line.encode()).hexdigest()})
            elif key in _INT_FIELDS:
                field = _INT_FIELDS[key]
                try:
                    value = int(raw)
                except ValueError:
                    work.append(f"line:{source.source_id}:{line_number}:{field}:clarify_integer")
                    continue
                if value < 0 or (field == "max_files_changed" and value < 1):
                    work.append(f"line:{source.source_id}:{line_number}:{field}:supply_nonnegative_limit")
                    continue
                int_values[field].append(value)
                provenance.append({"field": field, "values": [value], "source_id": source.source_id, "source_uri": source.source_uri, "line": line_number, "line_digest": hashlib.sha256(line.encode()).hexdigest()})
            elif key in _BOOL_FIELDS:
                field = _BOOL_FIELDS[key]
                value, issue = _parse_bool(raw)
                if issue:
                    work.append(f"line:{source.source_id}:{line_number}:{field}:{issue}")
                    continue
                bool_values[field].append(bool(value))
                provenance.append({"field": field, "values": [bool(value)], "source_id": source.source_id, "source_uri": source.source_uri, "line": line_number, "line_digest": hashlib.sha256(line.encode()).hexdigest()})

    required = list_values["required_paths"]
    forbidden = list_values["forbidden_paths"]
    for path in sorted(required.intersection(forbidden)):
        work.append("resolve_path_contradiction:" + path)
    allowed = list_values["allowed_paths"]
    if allowed:
        for path in sorted(path for path in required if path not in allowed and not any(_glob_could_cover(path, pattern) for pattern in allowed)):
            work.append("reconcile_required_path_with_allowed_surface:" + path)

    bool_contract: dict[str, bool] = {}
    for field, values in bool_values.items():
        if values and any(value != values[0] for value in values):
            work.append("resolve_boolean_contradiction:" + field)
        bool_contract[field] = values[0] if values else False

    contract: dict[str, Any] = {field: sorted(values) for field, values in list_values.items()}
    contract["required_symbols"] = {path: sorted(values) for path, values in sorted(symbols.items())}
    # Retain explicit limits only. Missing evidence is no longer replaced with hidden maxima.
    contract["max_files_changed"] = min(int_values["max_files_changed"]) if int_values["max_files_changed"] else None
    contract["max_deletions"] = min(int_values["max_deletions"]) if int_values["max_deletions"] else None
    contract.update(bool_contract)
    if not required and not contract["required_tests"] and not contract["required_symbols"]:
        work.append("supply_material_requirement")

    source_rows = [
        {"source_id": source.source_id if isinstance(source, IntentEvidence) else f"row-{index}", "source_uri": source.source_uri if isinstance(source, IntentEvidence) else "unavailable", "text_sha256": hashlib.sha256(source.text.encode()).hexdigest() if isinstance(source, IntentEvidence) and isinstance(source.text, str) else "unavailable"}
        for index, source in enumerate(sources)
    ]
    return RecoveredIntent(
        contract=contract,
        provenance=tuple(sorted(provenance, key=lambda row: (row["field"], row["source_id"], row["line"]))),
        source_digest=_digest(source_rows),
        contract_digest=_digest(contract),
        continuation="enabled",
        resolution_work=tuple(sorted(set(work))),
    )


def _glob_could_cover(path: str, pattern: str) -> bool:
    if path == pattern:
        return True
    if pattern.endswith("/**") and path.startswith(pattern[:-3].rstrip("/") + "/"):
        return True
    if "*" in pattern:
        return path.startswith(pattern.split("*", 1)[0])
    return False
