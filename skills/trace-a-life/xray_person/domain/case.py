"""Read-only accessors and reference indexes for a person-research case.

This module deliberately does not infer or validate facts.  It preserves the
input document, provides stable collection access, and indexes only explicit
IDs so later research stages can report missing references without silently
repairing them.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "trace-a-life/1"


class CaseFormatError(ValueError):
    """Raised when a case cannot be represented as the supported data model."""


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CaseFormatError(f"{field} must be an object")
    return MappingProxyType(deepcopy(dict(value)))


def _records(value: Any, field: str) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise CaseFormatError(f"{field} must be an array")
    records: list[Mapping[str, Any]] = []
    for position, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise CaseFormatError(f"{field}[{position}] must be an object")
        records.append(MappingProxyType(deepcopy(dict(item))))
    return tuple(records)


@dataclass(frozen=True)
class ReferenceIndex:
    """Explicit IDs from a case, plus duplicate IDs that were not overwritten."""

    sources: Mapping[str, Mapping[str, Any]]
    claims: Mapping[str, Mapping[str, Any]]
    events: Mapping[str, Mapping[str, Any]]
    decisions: Mapping[str, Mapping[str, Any]]
    contexts: Mapping[str, Mapping[str, Any]]
    interviews: Mapping[str, Mapping[str, Any]]
    oral_disclosures: Mapping[str, Mapping[str, Any]]
    relationship_nodes: Mapping[str, Mapping[str, Any]]
    relationship_edges: Mapping[str, Mapping[str, Any]]
    duplicate_ids: Mapping[str, tuple[str, ...]]

    def get(self, collection: str, reference_id: str) -> Mapping[str, Any] | None:
        """Resolve an ID within a named collection without cross-type guessing."""

        table = getattr(self, collection, None)
        if not isinstance(table, Mapping):
            raise KeyError(f"unknown reference collection: {collection}")
        return table.get(reference_id)


def _index_records(
    records: Iterable[Mapping[str, Any]],
) -> tuple[Mapping[str, Mapping[str, Any]], tuple[str, ...]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    duplicates: list[str] = []
    for record in records:
        record_id = record.get("id")
        if not isinstance(record_id, str) or not record_id:
            continue
        if record_id in indexed:
            duplicates.append(record_id)
            continue
        indexed[record_id] = record
    return MappingProxyType(indexed), tuple(dict.fromkeys(duplicates))


@dataclass(frozen=True)
class CaseDocument:
    """Read-only projection of a case JSON object.

    The constructor copies the input so callers cannot change research state
    behind the document.  ``as_dict`` returns another copy for deliberate
    serialization or transformation.
    """

    _data: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "CaseDocument":
        if not isinstance(data, Mapping):
            raise CaseFormatError("case root must be an object")
        copied = deepcopy(dict(data))
        if copied.get("schema_version") != SCHEMA_VERSION:
            raise CaseFormatError(f"schema_version must be {SCHEMA_VERSION}")
        subject = copied.get("subject")
        if not isinstance(subject, Mapping) or not subject.get("name"):
            raise CaseFormatError("subject.name is required")
        return cls(MappingProxyType(copied))

    @classmethod
    def from_path(cls, path: str | Path) -> "CaseDocument":
        case_path = Path(path)
        try:
            data = json.loads(case_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CaseFormatError(f"cannot read case {case_path}: {exc}") from exc
        if not isinstance(data, Mapping):
            raise CaseFormatError("case root must be an object")
        return cls.from_mapping(data)

    @property
    def schema_version(self) -> str:
        return str(self._data["schema_version"])

    @property
    def subject(self) -> Mapping[str, Any]:
        return _mapping(self._data.get("subject", {}), "subject")

    @property
    def scope(self) -> Mapping[str, Any]:
        return _mapping(self._data.get("scope", {}), "scope")

    @property
    def provenance(self) -> Mapping[str, Any]:
        return _mapping(self._data.get("provenance", {}), "provenance")

    @property
    def sources(self) -> tuple[Mapping[str, Any], ...]:
        return _records(self._data.get("sources", []), "sources")

    @property
    def claims(self) -> tuple[Mapping[str, Any], ...]:
        return _records(self._data.get("claims", []), "claims")

    @property
    def events(self) -> tuple[Mapping[str, Any], ...]:
        return _records(self._data.get("events", []), "events")

    @property
    def decisions(self) -> tuple[Mapping[str, Any], ...]:
        return _records(self._data.get("decisions", []), "decisions")

    @property
    def contexts(self) -> tuple[Mapping[str, Any], ...]:
        return _records(self._data.get("contexts", []), "contexts")

    @property
    def oral_history(self) -> Mapping[str, Any]:
        return _mapping(self._data.get("oral_history", {}), "oral_history")

    @property
    def interviews(self) -> tuple[Mapping[str, Any], ...]:
        return _records(self.oral_history.get("interviews", []), "oral_history.interviews")

    @property
    def oral_disclosures(self) -> tuple[Mapping[str, Any], ...]:
        return _records(self.oral_history.get("disclosures", []), "oral_history.disclosures")

    @property
    def relationships(self) -> Mapping[str, Any]:
        return _mapping(self._data.get("relationships", {}), "relationships")

    @property
    def relationship_nodes(self) -> tuple[Mapping[str, Any], ...]:
        return _records(self.relationships.get("nodes", []), "relationships.nodes")

    @property
    def relationship_edges(self) -> tuple[Mapping[str, Any], ...]:
        return _records(self.relationships.get("edges", []), "relationships.edges")

    @property
    def unresolved_questions(self) -> tuple[Mapping[str, Any], ...]:
        return _records(self._data.get("unresolved_questions", []), "unresolved_questions")

    @property
    def index(self) -> ReferenceIndex:
        collections = {
            "sources": self.sources,
            "claims": self.claims,
            "events": self.events,
            "decisions": self.decisions,
            "contexts": self.contexts,
            "interviews": self.interviews,
            "oral_disclosures": self.oral_disclosures,
            "relationship_nodes": self.relationship_nodes,
            "relationship_edges": self.relationship_edges,
        }
        indexes: dict[str, Mapping[str, Mapping[str, Any]]] = {}
        duplicates: dict[str, tuple[str, ...]] = {}
        for name, records in collections.items():
            index, duplicate_ids = _index_records(records)
            indexes[name] = index
            if duplicate_ids:
                duplicates[name] = duplicate_ids
        return ReferenceIndex(
            **indexes,
            duplicate_ids=MappingProxyType(duplicates),
        )

    def as_dict(self) -> dict[str, Any]:
        return deepcopy(dict(self._data))


def load_case(path: str | Path) -> CaseDocument:
    """Load a case from disk; convenience alias for CLI and orchestrator code."""

    return CaseDocument.from_path(path)
