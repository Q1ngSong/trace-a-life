"""Hash-addressed Agent handoff envelopes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .stages import PipelineStage


HANDOFF_SCHEMA = "xray-handoff/1"
HANDOFF_STATUSES = frozenset({"ready", "blocked", "rejected"})


def file_sha256(path: Path) -> str:
    """Hash a file without loading the whole artifact into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_artifact_path(case_dir: Path, path: Path) -> str:
    resolved_case = case_dir.resolve()
    resolved_path = path.resolve()
    try:
        relative = resolved_path.relative_to(resolved_case)
    except ValueError as exc:
        raise ValueError(f"artifact must stay inside case directory: {path}") from exc
    return PurePosixPath(relative).as_posix()


@dataclass(frozen=True)
class ArtifactRef:
    kind: str
    path: str
    sha256: str

    @classmethod
    def from_file(cls, *, kind: str, case_dir: Path, path: Path) -> "ArtifactRef":
        if not kind.strip():
            raise ValueError("artifact kind is required")
        if not path.is_file():
            raise ValueError(f"artifact file does not exist: {path}")
        return cls(
            kind=kind.strip(),
            path=_relative_artifact_path(case_dir, path),
            sha256=file_sha256(path),
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ArtifactRef":
        return cls(kind=str(value["kind"]), path=str(value["path"]), sha256=str(value["sha256"]))

    def verify(self, case_dir: Path) -> tuple[bool, str]:
        candidate = case_dir / self.path
        if not candidate.is_file():
            return False, f"missing artifact: {self.path}"
        actual = file_sha256(candidate)
        if actual != self.sha256:
            return False, f"hash drift: {self.path} expected {self.sha256}, got {actual}"
        return True, "ok"


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    detail: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "GateResult":
        return cls(name=str(value["name"]), passed=bool(value["passed"]), detail=str(value.get("detail", "")))


@dataclass(frozen=True)
class Handoff:
    run_id: str
    case_id: str
    stage: str
    producer: str
    consumer: str
    status: str
    inputs: tuple[ArtifactRef, ...] = field(default_factory=tuple)
    outputs: tuple[ArtifactRef, ...] = field(default_factory=tuple)
    gates: tuple[GateResult, ...] = field(default_factory=tuple)
    unresolved: tuple[str, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    schema_version: str = HANDOFF_SCHEMA

    def __post_init__(self) -> None:
        PipelineStage(self.stage)
        if self.schema_version != HANDOFF_SCHEMA:
            raise ValueError(f"unsupported handoff schema: {self.schema_version}")
        if self.status not in HANDOFF_STATUSES:
            raise ValueError(f"invalid handoff status: {self.status}")
        for label, value in (("run_id", self.run_id), ("case_id", self.case_id), ("producer", self.producer), ("consumer", self.consumer)):
            if not value.strip():
                raise ValueError(f"{label} is required")
        if self.status == "ready" and not self.outputs:
            raise ValueError("ready handoff requires at least one output")
        if self.status == "ready" and any(not gate.passed for gate in self.gates):
            raise ValueError("ready handoff cannot contain a failed gate")
        if self.status == "blocked" and not self.unresolved:
            raise ValueError("blocked handoff must explain at least one unresolved item")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Handoff":
        return cls(
            schema_version=str(value.get("schema_version", "")),
            run_id=str(value["run_id"]),
            case_id=str(value["case_id"]),
            stage=str(value["stage"]),
            producer=str(value["producer"]),
            consumer=str(value["consumer"]),
            status=str(value["status"]),
            inputs=tuple(ArtifactRef.from_dict(item) for item in value.get("inputs", [])),
            outputs=tuple(ArtifactRef.from_dict(item) for item in value.get("outputs", [])),
            gates=tuple(GateResult.from_dict(item) for item in value.get("gates", [])),
            unresolved=tuple(str(item) for item in value.get("unresolved", [])),
            notes=tuple(str(item) for item in value.get("notes", [])),
            created_at=str(value["created_at"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def verify_artifacts(self, case_dir: Path, *, include_inputs: bool = True) -> list[str]:
        issues: list[str] = []
        refs: Iterable[ArtifactRef] = self.inputs + self.outputs if include_inputs else self.outputs
        for artifact in refs:
            valid, message = artifact.verify(case_dir)
            if not valid:
                issues.append(message)
        return issues


def write_handoff(handoff: Handoff, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(handoff.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_handoff(path: Path) -> Handoff:
    return Handoff.from_dict(json.loads(path.read_text(encoding="utf-8")))
