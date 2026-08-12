"""Recover a Patch Intent Twin contract from explicit issue/PR evidence.

The adapter is deliberately deterministic. It extracts only recognized,
explicitly stated constraints from Markdown/text evidence and records exactly
which source line produced each contract field. Ambiguous or contradictory
material constraints fail closed instead of being guessed.
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
_INT_FIELDS = {
    "max files changed": "max_files_changed",
    "max deletions": "max_deletions",
}
_BOOL_FIELDS = {"allow file deletion": "allow_file_deletion"}
_SYMBOL_RE = re.compile(r"^required symbols?\s+(.+?)\s*:\s*(.+)$", re.I)
_KEY_VALUE_RE = re.compile(r"^\s*(?:[-*]\s*)?([^:]+?)\s*:\s*(.*?)\s*$")


def _digest(value: Any) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _split_values(raw: str) -> list[str]:
    value = raw.strip()
    if value.startswith("["):
        parsed = json.loads(value)
        if not isinstance(parsed, list) or not all(isinstance(item, str) and item.strip() for item in parsed):
            raise ValueError("intent_list_invalid")
        return [item.strip() for item in parsed]
    return [item.strip() for item in re.split(r"\s*,\s*|\s*;\s*", value) if item.strip()]


def _parse_bool(raw: str) -> bool:
    value = raw.strip().lower()
    if value in {"true", "yes", "allow", "allowed"}:
        return True
    if value in {"false", "no", "deny", "denied", "forbid", "forbidden"}:
        return False
    raise ValueError("intent_boolean_invalid")


@dataclass(frozen=True)
class IntentEvidence:
    source_id: str
    source_uri: str
    text: str

    def validate(self) -> None:
        if not self.source_id.strip():
            raise ValueError("intent_source_id_missing")
        if not self.source_uri.strip():
            raise ValueError("intent_source_uri_missing")
        if not self.text.strip():
            raise ValueError("intent_source_text_missing")


@dataclass(frozen=True)
class RecoveredIntent:
    contract: dict[str, Any]
    provenance: tuple[dict[str, Any], ...]
    source_digest: str
    contract_digest: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract": self.contract,
            "provenance": [dict(row) for row in self.provenance],
            "source_digest": self.source_digest,
            "contract_digest": self.contract_digest,
        }


def recover_intent(evidence: Iterable[IntentEvidence]) -> RecoveredIntent:
    sources = list(evidence)
    if not sources:
        raise ValueError("intent_evidence_empty")
    for source in sources:
        source.validate()

    list_values: dict[str, set[str]] = {field: set() for field in _LIST_FIELDS.values()}
    int_values: dict[str, list[int]] = {field: [] for field in _INT_FIELDS.values()}
    bool_values: dict[str, list[bool]] = {field: [] for field in _BOOL_FIELDS.values()}
    symbols: dict[str, set[str]] = {}
    provenance: list[dict[str, Any]] = []

    for source in sources:
        for line_number, line in enumerate(source.text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            symbol_match = _SYMBOL_RE.match(stripped.lstrip("-* "))
            if symbol_match:
                path = symbol_match.group(1).strip().strip("`")
                values = _split_values(symbol_match.group(2))
                if not path or not values:
                    raise ValueError("required_symbols_invalid")
                symbols.setdefault(path, set()).update(values)
                provenance.append({
                    "field": f"required_symbols.{path}",
                    "values": values,
                    "source_id": source.source_id,
                    "source_uri": source.source_uri,
                    "line": line_number,
                    "line_digest": hashlib.sha256(line.encode("utf-8")).hexdigest(),
                })
                continue
            match = _KEY_VALUE_RE.match(stripped)
            if not match:
                continue
            key = re.sub(r"\s+", " ", match.group(1).strip().lower().strip("`"))
            raw = match.group(2).strip()
            if key in _LIST_FIELDS:
                field = _LIST_FIELDS[key]
                values = _split_values(raw)
                if not values:
                    raise ValueError(f"{field}_empty")
                list_values[field].update(values)
                provenance.append({
                    "field": field,
                    "values": values,
                    "source_id": source.source_id,
                    "source_uri": source.source_uri,
                    "line": line_number,
                    "line_digest": hashlib.sha256(line.encode("utf-8")).hexdigest(),
                })
            elif key in _INT_FIELDS:
                field = _INT_FIELDS[key]
                try:
                    value = int(raw)
                except ValueError as exc:
                    raise ValueError(f"{field}_invalid") from exc
                if value < 0 or (field == "max_files_changed" and value < 1):
                    raise ValueError(f"{field}_invalid")
                int_values[field].append(value)
                provenance.append({
                    "field": field,
                    "values": [value],
                    "source_id": source.source_id,
                    "source_uri": source.source_uri,
                    "line": line_number,
                    "line_digest": hashlib.sha256(line.encode("utf-8")).hexdigest(),
                })
            elif key in _BOOL_FIELDS:
                field = _BOOL_FIELDS[key]
                value = _parse_bool(raw)
                bool_values[field].append(value)
                provenance.append({
                    "field": field,
                    "values": [value],
                    "source_id": source.source_id,
                    "source_uri": source.source_uri,
                    "line": line_number,
                    "line_digest": hashlib.sha256(line.encode("utf-8")).hexdigest(),
                })

    required = list_values["required_paths"]
    forbidden = list_values["forbidden_paths"]
    exact_conflict = sorted(required.intersection(forbidden))
    if exact_conflict:
        raise ValueError("intent_path_contradiction:" + ",".join(exact_conflict))
    allowed = list_values["allowed_paths"]
    if allowed:
        outside = sorted(path for path in required if path not in allowed and not any(_glob_could_cover(path, pattern) for pattern in allowed))
        if outside:
            raise ValueError("required_path_outside_allowed_surface:" + ",".join(outside))

    bool_contract: dict[str, bool] = {}
    for field, values in bool_values.items():
        if values and any(value != values[0] for value in values):
            raise ValueError(f"intent_boolean_contradiction:{field}")
        bool_contract[field] = values[0] if values else False

    contract: dict[str, Any] = {
        field: sorted(values) for field, values in list_values.items()
    }
    contract["required_symbols"] = {path: sorted(values) for path, values in sorted(symbols.items())}
    contract["max_files_changed"] = min(int_values["max_files_changed"]) if int_values["max_files_changed"] else 50
    contract["max_deletions"] = min(int_values["max_deletions"]) if int_values["max_deletions"] else 10_000
    contract.update(bool_contract)

    if not required and not contract["required_tests"] and not contract["required_symbols"]:
        raise ValueError("intent_material_requirements_not_recovered")

    source_rows = [
        {
            "source_id": source.source_id,
            "source_uri": source.source_uri,
            "text_sha256": hashlib.sha256(source.text.encode("utf-8")).hexdigest(),
        }
        for source in sorted(sources, key=lambda item: (item.source_id, item.source_uri))
    ]
    return RecoveredIntent(
        contract=contract,
        provenance=tuple(sorted(provenance, key=lambda row: (row["field"], row["source_id"], row["line"]))),
        source_digest=_digest(source_rows),
        contract_digest=_digest(contract),
    )


def _glob_could_cover(path: str, pattern: str) -> bool:
    if path == pattern:
        return True
    if pattern.endswith("/**") and path.startswith(pattern[:-3].rstrip("/") + "/"):
        return True
    if "*" in pattern:
        prefix = pattern.split("*", 1)[0]
        return path.startswith(prefix)
    return False
