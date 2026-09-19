"""Version-pinned handoff model for MiroFish simulation.

The adapter validates an exported seed package and models the multi-stage flow
found in the vendored MiroFish snapshot.  It does not send HTTP requests: the
running instance's request bodies and version must still be verified before an
executor is authorized.  All recovered artifacts remain simulations.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping

from .contracts import (
    ExecutorAttestation,
    IntegrationContractError,
    canonical_json,
    payload_sha256,
)


SEED_SCHEMA_VERSION = "x-ray-mirofish-seed/1"
ALLOWED_SEED_CLAIM_STATUSES = frozenset({"verified", "credible", "partial"})


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IntegrationContractError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_aware_timestamp(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IntegrationContractError(f"{field_name} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise IntegrationContractError(f"{field_name} must include a timezone")
    return text


def _bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_digest(value: object, field_name: str) -> str:
    digest = _require_text(value, field_name).lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise IntegrationContractError(f"{field_name} must be a SHA-256 digest")
    return digest


def _freeze_manifest_value(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_manifest_value(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_manifest_value(item) for item in value)
    return value


def _freeze_manifest(value: Mapping[str, Any]) -> Mapping[str, Any]:
    # canonical_json performs recursive JSON-type validation and makes a copy.
    copied = json.loads(canonical_json(value))
    return _freeze_manifest_value(copied)


@dataclass(frozen=True)
class SeedPackage:
    """Validated MiroFish input exported from a frozen case."""

    root: Path
    manifest_path: Path
    seed_path: Path
    manifest: Mapping[str, Any]
    seed_text: str
    manifest_sha256: str
    seed_sha256: str
    source_case_sha256: str | None
    package_sha256: str
    actual_claim_statuses: tuple[str, ...]
    binding_status: str
    binding_notes: tuple[str, ...]
    epistemic_status: str = field(default="simulation-input", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "manifest_path": str(self.manifest_path),
            "seed_path": str(self.seed_path),
            "manifest": json.loads(canonical_json(self.manifest)),
            "manifest_sha256": self.manifest_sha256,
            "seed_sha256": self.seed_sha256,
            "source_case_sha256": self.source_case_sha256,
            "package_sha256": self.package_sha256,
            "actual_claim_statuses": list(self.actual_claim_statuses),
            "binding_status": self.binding_status,
            "binding_notes": list(self.binding_notes),
            "epistemic_status": self.epistemic_status,
        }


_SEED_CLAIM = re.compile(
    r"(?m)^- \[(?P<status>[a-z_]+)\] (?P<text>.*?)(?:（来源：.*)?$"
)


def _load_frozen_case(value: str | Path | Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    if isinstance(value, Mapping):
        copied = json.loads(canonical_json(value))
        return copied, payload_sha256(copied)
    path = Path(value).expanduser().resolve()
    try:
        raw_bytes = path.read_bytes()
        parsed = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IntegrationContractError(f"cannot read frozen case: {exc}") from exc
    if not isinstance(parsed, dict):
        raise IntegrationContractError("frozen case must be a JSON object")
    return parsed, _bytes_sha256(raw_bytes)


def load_seed_package(
    location: str | Path,
    *,
    frozen_case: str | Path | Mapping[str, Any] | None = None,
    expected_subject: str | None = None,
    expected_fact_freeze_at: str | None = None,
    expected_case_sha256: str | None = None,
    expected_seed_sha256: str | None = None,
) -> SeedPackage:
    """Load a seed and, when supplied, bind it to the Leader's frozen case.

    Legacy manifests remain readable, but without ``frozen_case`` or trusted
    expected digests the returned package is ``unverified`` and cannot create a
    :class:`SimulationPlan`.
    """

    location = Path(location).expanduser().resolve()
    manifest_path = location if location.is_file() else location / "simulation-manifest.json"
    if not manifest_path.is_file():
        raise IntegrationContractError(f"missing MiroFish manifest: {manifest_path}")
    root = manifest_path.parent.resolve()
    try:
        manifest_bytes = manifest_path.read_bytes()
        raw = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IntegrationContractError(f"cannot read MiroFish manifest: {exc}") from exc
    if not isinstance(raw, dict):
        raise IntegrationContractError("MiroFish manifest must be a JSON object")

    if raw.get("schema_version") != SEED_SCHEMA_VERSION:
        raise IntegrationContractError(
            f"unsupported seed schema_version: {raw.get('schema_version')!r}"
        )
    for field_name in ("subject", "question", "seed_file"):
        raw[field_name] = _require_text(raw.get(field_name), field_name)
    raw["fact_freeze_at"] = _require_aware_timestamp(
        raw.get("fact_freeze_at"), "fact_freeze_at"
    )
    if raw.get("epistemic_status") != "simulation-input":
        raise IntegrationContractError("seed epistemic_status must be simulation-input")
    claim_statuses = raw.get("allowed_claim_statuses")
    if not isinstance(claim_statuses, list) or not claim_statuses:
        raise IntegrationContractError("allowed_claim_statuses must be a non-empty list")
    normalized_statuses = {_require_text(item, "allowed_claim_statuses[]") for item in claim_statuses}
    if not normalized_statuses.issubset(ALLOWED_SEED_CLAIM_STATUSES):
        invalid = sorted(normalized_statuses - ALLOWED_SEED_CLAIM_STATUSES)
        raise IntegrationContractError(
            f"seed contains disallowed claim status(es): {', '.join(invalid)}"
        )
    raw["allowed_claim_statuses"] = sorted(normalized_statuses)

    seed_path = (root / raw["seed_file"]).resolve()
    try:
        seed_path.relative_to(root)
    except ValueError as exc:
        raise IntegrationContractError("seed_file must remain inside the package directory") from exc
    if not seed_path.is_file():
        raise IntegrationContractError(f"missing MiroFish seed document: {seed_path}")
    try:
        seed_text = seed_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise IntegrationContractError(f"cannot read MiroFish seed document: {exc}") from exc
    if not seed_text.strip():
        raise IntegrationContractError("MiroFish seed document is empty")

    manifest_sha = _bytes_sha256(manifest_bytes)
    seed_sha = _bytes_sha256(seed_text.encode("utf-8"))
    if raw.get("seed_sha256") is not None:
        declared_seed_sha = _require_digest(raw["seed_sha256"], "seed_sha256")
        if declared_seed_sha != seed_sha:
            raise IntegrationContractError("seed_sha256 does not match the seed document")
    if expected_seed_sha256 is not None and _require_digest(
        expected_seed_sha256, "expected_seed_sha256"
    ) != seed_sha:
        raise IntegrationContractError("seed document does not match expected_seed_sha256")

    seed_claims = [
        (match.group("status"), match.group("text").strip())
        for match in _SEED_CLAIM.finditer(seed_text)
    ]
    actual_statuses = {status for status, _ in seed_claims}
    disallowed_actual = actual_statuses - ALLOWED_SEED_CLAIM_STATUSES
    if disallowed_actual:
        raise IntegrationContractError(
            "seed body contains disallowed claim status(es): "
            + ", ".join(sorted(disallowed_actual))
        )
    undeclared_actual = actual_statuses - normalized_statuses
    if undeclared_actual:
        raise IntegrationContractError(
            "seed body uses status(es) absent from manifest: "
            + ", ".join(sorted(undeclared_actual))
        )

    expected_subject = (
        _require_text(expected_subject, "expected_subject")
        if expected_subject is not None
        else None
    )
    expected_fact_freeze_at = (
        _require_aware_timestamp(expected_fact_freeze_at, "expected_fact_freeze_at")
        if expected_fact_freeze_at is not None
        else None
    )
    expected_case_sha256 = (
        _require_digest(expected_case_sha256, "expected_case_sha256")
        if expected_case_sha256 is not None
        else None
    )
    case_sha: str | None = None
    binding_notes: list[str] = []
    binding_status = "unverified"
    if frozen_case is not None:
        case, case_sha = _load_frozen_case(frozen_case)
        freeze = case.get("fact_freeze")
        subject = case.get("subject")
        if not isinstance(freeze, Mapping) or freeze.get("status") != "frozen":
            raise IntegrationContractError("source case is not fact-frozen")
        case_freeze_at = _require_aware_timestamp(
            freeze.get("frozen_at"), "source_case.fact_freeze.frozen_at"
        )
        if not isinstance(subject, Mapping):
            raise IntegrationContractError("source case has no subject object")
        case_subject = _require_text(subject.get("name"), "source_case.subject.name")
        if case_subject != raw["subject"]:
            raise IntegrationContractError("seed subject does not match source case")
        if case_freeze_at != raw["fact_freeze_at"]:
            raise IntegrationContractError("seed fact_freeze_at does not match source case")
        case_claims = {
            (str(claim.get("status", "")), str(claim.get("text", "")).strip())
            for claim in case.get("claims", [])
            if isinstance(claim, Mapping)
        }
        missing_claims = [claim for claim in seed_claims if claim not in case_claims]
        if missing_claims:
            raise IntegrationContractError(
                "seed contains claim(s) not found with the same status in source case"
            )
        if raw.get("source_case_sha256") is not None and _require_digest(
            raw["source_case_sha256"], "source_case_sha256"
        ) != case_sha:
            raise IntegrationContractError("manifest source_case_sha256 does not match case")
        if expected_case_sha256 is not None and expected_case_sha256 != case_sha:
            raise IntegrationContractError("source case does not match expected_case_sha256")
        if expected_subject is not None and expected_subject != case_subject:
            raise IntegrationContractError("source case does not match expected_subject")
        if expected_fact_freeze_at is not None and expected_fact_freeze_at != case_freeze_at:
            raise IntegrationContractError(
                "source case does not match expected_fact_freeze_at"
            )
        binding_status = "verified"
        binding_notes.append("seed subject, freeze, claims and case digest verified")
    else:
        if expected_subject is not None and expected_subject != raw["subject"]:
            raise IntegrationContractError("seed does not match expected_subject")
        if (
            expected_fact_freeze_at is not None
            and expected_fact_freeze_at != raw["fact_freeze_at"]
        ):
            raise IntegrationContractError("seed does not match expected_fact_freeze_at")
        declared_case_sha = raw.get("source_case_sha256")
        if expected_case_sha256 is not None:
            if declared_case_sha is None:
                binding_notes.append(
                    "legacy manifest has no source_case_sha256; frozen_case is required"
                )
            elif _require_digest(declared_case_sha, "source_case_sha256") != expected_case_sha256:
                raise IntegrationContractError("manifest does not match expected_case_sha256")
            elif expected_subject is not None and expected_fact_freeze_at is not None:
                case_sha = expected_case_sha256
                binding_status = "verified"
                binding_notes.append("trusted expected subject, freeze and case digest matched")
        if binding_status != "verified":
            binding_notes.append("no current frozen case was supplied by the Leader")

    frozen = _freeze_manifest(raw)
    digest = payload_sha256(
        {
            "manifest_sha256": manifest_sha,
            "seed_sha256": seed_sha,
            "source_case_sha256": case_sha,
        }
    )
    return SeedPackage(
        root=root,
        manifest_path=manifest_path,
        seed_path=seed_path,
        manifest=frozen,
        seed_text=seed_text,
        manifest_sha256=manifest_sha,
        seed_sha256=seed_sha,
        source_case_sha256=case_sha,
        package_sha256=digest,
        actual_claim_statuses=tuple(sorted(actual_statuses)),
        binding_status=binding_status,
        binding_notes=tuple(binding_notes),
    )


class SimulationStage(str, Enum):
    ONTOLOGY_GENERATION = "ontology_generation"
    GRAPH_BUILD = "graph_build"
    GRAPH_BUILD_POLL = "graph_build_poll"
    SIMULATION_CREATE = "simulation_create"
    SIMULATION_PREPARE = "simulation_prepare"
    SIMULATION_PREPARE_POLL = "simulation_prepare_poll"
    SIMULATION_START = "simulation_start"
    SIMULATION_RUN_POLL = "simulation_run_poll"
    REPORT_GENERATE = "report_generate"
    REPORT_GENERATE_POLL = "report_generate_poll"
    REPORT_RETRIEVE = "report_retrieve"


class StageStatus(str, Enum):
    BLOCKED = "blocked"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class StageRequirement:
    output_kind_alternatives: tuple[str, ...]
    terminal_field: str | None = None
    terminal_values: tuple[str, ...] = ()


@dataclass(frozen=True)
class StageDefinition:
    stage: SimulationStage
    method: str
    endpoint_template: str
    purpose: str
    output_epistemic_status: str = field(default="simulation", init=False)

    def to_dict(self) -> dict[str, str]:
        return {
            "stage": self.stage.value,
            "method": self.method,
            "endpoint_template": self.endpoint_template,
            "purpose": self.purpose,
            "output_epistemic_status": self.output_epistemic_status,
        }


# Paths and methods are copied from the vendored Flask routes.  They are a plan,
# not authorization to call an arbitrary or unverified MiroFish deployment.
MIROFISH_STAGE_DEFINITIONS: tuple[StageDefinition, ...] = (
    StageDefinition(
        SimulationStage.ONTOLOGY_GENERATION,
        "POST",
        "/api/graph/ontology/generate",
        "upload frozen seed and generate ontology",
    ),
    StageDefinition(
        SimulationStage.GRAPH_BUILD,
        "POST",
        "/api/graph/build",
        "build the graph and obtain a task id",
    ),
    StageDefinition(
        SimulationStage.GRAPH_BUILD_POLL,
        "GET",
        "/api/graph/task/{task_id}",
        "wait for graph construction to reach a terminal state",
    ),
    StageDefinition(
        SimulationStage.SIMULATION_CREATE,
        "POST",
        "/api/simulation/create",
        "create a simulation bound to the verified graph id",
    ),
    StageDefinition(
        SimulationStage.SIMULATION_PREPARE,
        "POST",
        "/api/simulation/prepare",
        "generate agent profiles and simulation configuration",
    ),
    StageDefinition(
        SimulationStage.SIMULATION_PREPARE_POLL,
        "POST",
        "/api/simulation/prepare/status",
        "wait for preparation to reach a terminal state",
    ),
    StageDefinition(
        SimulationStage.SIMULATION_START,
        "POST",
        "/api/simulation/start",
        "start the prepared multi-agent simulation",
    ),
    StageDefinition(
        SimulationStage.SIMULATION_RUN_POLL,
        "GET",
        "/api/simulation/{simulation_id}/run-status",
        "wait for simulation execution to reach a terminal state",
    ),
    StageDefinition(
        SimulationStage.REPORT_GENERATE,
        "POST",
        "/api/report/generate",
        "request an analysis report for the simulation",
    ),
    StageDefinition(
        SimulationStage.REPORT_GENERATE_POLL,
        "POST",
        "/api/report/generate/status",
        "wait for report generation to reach a terminal state",
    ),
    StageDefinition(
        SimulationStage.REPORT_RETRIEVE,
        "GET",
        "/api/report/{report_id}",
        "retrieve the generated simulation report",
    ),
)


_STAGE_REQUIREMENTS: Mapping[SimulationStage, StageRequirement] = MappingProxyType(
    {
        SimulationStage.ONTOLOGY_GENERATION: StageRequirement(("project",)),
        SimulationStage.GRAPH_BUILD: StageRequirement(("task",)),
        SimulationStage.GRAPH_BUILD_POLL: StageRequirement(
            ("graph",), "status", ("completed", "success")
        ),
        SimulationStage.SIMULATION_CREATE: StageRequirement(("simulation",)),
        SimulationStage.SIMULATION_PREPARE: StageRequirement(("simulation",)),
        SimulationStage.SIMULATION_PREPARE_POLL: StageRequirement(
            ("task", "simulation"), "status", ("completed", "ready", "success")
        ),
        SimulationStage.SIMULATION_START: StageRequirement(("simulation",)),
        SimulationStage.SIMULATION_RUN_POLL: StageRequirement(
            ("simulation",), "runner_status", ("completed", "finished")
        ),
        SimulationStage.REPORT_GENERATE: StageRequirement(("task",)),
        SimulationStage.REPORT_GENERATE_POLL: StageRequirement(
            ("report",), "status", ("completed", "success")
        ),
        SimulationStage.REPORT_RETRIEVE: StageRequirement(
            ("report",), "status", ("completed",)
        ),
    }
)


_OUTPUT_ID_FIELDS: Mapping[str, str] = MappingProxyType(
    {
        "project": "project_id",
        "task": "task_id",
        "graph": "graph_id",
        "simulation": "simulation_id",
        "report": "report_id",
    }
)


def _find_response_value(value: Any, field_name: str) -> Any:
    if isinstance(value, Mapping):
        if field_name in value:
            return value[field_name]
        for item in value.values():
            found = _find_response_value(item, field_name)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for item in value:
            found = _find_response_value(item, field_name)
            if found is not None:
                return found
    return None


def _required_stage_refs(
    stage: SimulationStage, response: Mapping[str, Any]
) -> tuple[str, ...]:
    requirement = _STAGE_REQUIREMENTS[stage]
    for kind in requirement.output_kind_alternatives:
        value = _find_response_value(response, _OUTPUT_ID_FIELDS[kind])
        if value is not None and str(value).strip():
            return (f"{kind}:{str(value).strip()}",)
    expected = " or ".join(
        _OUTPUT_ID_FIELDS[kind] for kind in requirement.output_kind_alternatives
    )
    raise IntegrationContractError(f"stage {stage.value} response requires {expected}")


@dataclass(frozen=True)
class StageProgress:
    definition: StageDefinition
    status: StageStatus
    output_refs: tuple[str, ...] = ()
    response_sha256: str | None = None
    attestation: ExecutorAttestation | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = self.definition.to_dict()
        value.update(
            {
                "status": self.status.value,
                "output_refs": list(self.output_refs),
                "response_sha256": self.response_sha256,
                "attestation": self.attestation.to_dict() if self.attestation else None,
                "error": self.error,
            }
        )
        return value


@dataclass(frozen=True)
class SimulationPlan:
    """Immutable state machine supervised by the Leader Agent."""

    plan_id: str
    seed_package_sha256: str
    source_case_sha256: str
    fact_freeze_at: str
    question: str
    stages: tuple[StageProgress, ...]
    epistemic_status: str = field(default="simulation", init=False)
    execution_policy: str = field(default="explicit-executor-required", init=False)

    @classmethod
    def from_seed(cls, package: SeedPackage) -> "SimulationPlan":
        if package.binding_status != "verified" or package.source_case_sha256 is None:
            raise IntegrationContractError(
                "MiroFish plan requires a seed verified against the Leader's frozen case"
            )
        stages = tuple(
            StageProgress(
                definition,
                StageStatus.READY if index == 0 else StageStatus.BLOCKED,
            )
            for index, definition in enumerate(MIROFISH_STAGE_DEFINITIONS)
        )
        return cls(
            plan_id=f"mirofish-plan:{package.package_sha256[:20]}",
            seed_package_sha256=package.package_sha256,
            source_case_sha256=package.source_case_sha256,
            fact_freeze_at=str(package.manifest["fact_freeze_at"]),
            question=str(package.manifest["question"]),
            stages=stages,
        )

    @property
    def current(self) -> StageProgress | None:
        return next(
            (
                item
                for item in self.stages
                if item.status in {StageStatus.READY, StageStatus.RUNNING}
            ),
            None,
        )

    @property
    def status(self) -> str:
        if any(item.status == StageStatus.FAILED for item in self.stages):
            return "failed"
        if all(item.status == StageStatus.COMPLETED for item in self.stages):
            return "completed"
        if any(item.status == StageStatus.RUNNING for item in self.stages):
            return "running"
        return "requested"

    def start(self, stage: SimulationStage | str) -> "SimulationPlan":
        index = self._index(stage)
        if self.stages[index].status != StageStatus.READY:
            raise IntegrationContractError(
                f"stage {self.stages[index].definition.stage.value} is not ready"
            )
        return self._replace(index, replace(self.stages[index], status=StageStatus.RUNNING))

    def stage_request_sha256(
        self, stage: SimulationStage | str, request: Mapping[str, Any]
    ) -> str:
        resolved = self._resolve_stage(stage)
        if not isinstance(request, Mapping):
            raise IntegrationContractError("stage request must be a mapping")
        definition = self.stages[self._index(resolved)].definition
        return payload_sha256(
            {
                "provider": "mirofish",
                "plan_id": self.plan_id,
                "seed_package_sha256": self.seed_package_sha256,
                "stage": resolved.value,
                "method": definition.method,
                "endpoint_template": definition.endpoint_template,
                "request": request,
            }
        )

    def attest_stage_execution(
        self,
        stage: SimulationStage | str,
        *,
        request: Mapping[str, Any],
        response: Mapping[str, Any],
        executor: str,
        attempt_id: str,
        transport: str,
        executed_at: str,
        status_code: int,
        server_version: str,
    ) -> ExecutorAttestation:
        """Build an attestation at the HTTP executor boundary after execution."""

        if not isinstance(response, Mapping):
            raise IntegrationContractError("stage response must be a mapping")
        return ExecutorAttestation(
            executor=executor,
            attempt_id=attempt_id,
            transport=transport,
            executed_at=executed_at,
            request_sha256=self.stage_request_sha256(stage, request),
            response_sha256=payload_sha256(response),
            status_code=status_code,
            server_version=server_version,
        )

    def complete(
        self,
        stage: SimulationStage | str,
        *,
        request: Mapping[str, Any],
        response: Mapping[str, Any],
        attestation: ExecutorAttestation,
        output_refs: tuple[str, ...],
    ) -> "SimulationPlan":
        index = self._index(stage)
        current = self.stages[index]
        if current.status != StageStatus.RUNNING:
            raise IntegrationContractError(
                f"stage {current.definition.stage.value} must be running before completion"
            )
        if not isinstance(attestation, ExecutorAttestation):
            raise IntegrationContractError("stage completion requires ExecutorAttestation")
        if not isinstance(response, Mapping):
            raise IntegrationContractError("stage response must be a mapping")
        if attestation.request_sha256 != self.stage_request_sha256(stage, request):
            raise IntegrationContractError("stage attestation request digest mismatch")
        response_digest = payload_sha256(response)
        if attestation.response_sha256 != response_digest:
            raise IntegrationContractError("stage attestation response digest mismatch")
        if not 200 <= attestation.status_code < 300:
            raise IntegrationContractError("stage executor transport did not succeed")
        if response.get("success") is not True:
            raise IntegrationContractError("MiroFish response does not report success=true")
        if not isinstance(output_refs, (list, tuple)) or not output_refs:
            raise IntegrationContractError("stage completion requires non-empty output_refs")
        refs = tuple(_require_text(item, "output_refs[]") for item in output_refs)
        resolved_stage = current.definition.stage
        expected_refs = _required_stage_refs(resolved_stage, response)
        missing_refs = [item for item in expected_refs if item not in refs]
        if missing_refs:
            raise IntegrationContractError(
                "stage output_refs do not match attested response: " + ", ".join(missing_refs)
            )
        requirement = _STAGE_REQUIREMENTS[resolved_stage]
        if requirement.terminal_field is not None:
            terminal = _find_response_value(response, requirement.terminal_field)
            terminal_text = str(terminal or "").lower()
            if terminal_text not in requirement.terminal_values:
                raise IntegrationContractError(
                    f"stage {resolved_stage.value} is non-terminal: "
                    f"{requirement.terminal_field}={terminal!r}"
                )
        updated = list(self.stages)
        updated[index] = replace(
            current,
            status=StageStatus.COMPLETED,
            output_refs=refs,
            response_sha256=response_digest,
            attestation=attestation,
            error=None,
        )
        if index + 1 < len(updated):
            updated[index + 1] = replace(updated[index + 1], status=StageStatus.READY)
        return replace(self, stages=tuple(updated))

    def fail(self, stage: SimulationStage | str, *, error: str) -> "SimulationPlan":
        index = self._index(stage)
        current = self.stages[index]
        if current.status != StageStatus.RUNNING:
            raise IntegrationContractError(
                f"stage {current.definition.stage.value} must be running before failure"
            )
        return self._replace(
            index,
            replace(current, status=StageStatus.FAILED, error=_require_text(error, "error")),
        )

    def _index(self, stage: SimulationStage | str) -> int:
        resolved = self._resolve_stage(stage)
        for index, item in enumerate(self.stages):
            if item.definition.stage == resolved:
                return index
        raise IntegrationContractError(f"stage absent from plan: {resolved.value}")

    @staticmethod
    def _resolve_stage(stage: SimulationStage | str) -> SimulationStage:
        try:
            return stage if isinstance(stage, SimulationStage) else SimulationStage(stage)
        except ValueError as exc:
            raise IntegrationContractError(f"unsupported MiroFish stage: {stage}") from exc

    def _replace(self, index: int, value: StageProgress) -> "SimulationPlan":
        updated = list(self.stages)
        updated[index] = value
        return replace(self, stages=tuple(updated))

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": "x-ray-mirofish-plan/1",
            "plan_id": self.plan_id,
            "seed_package_sha256": self.seed_package_sha256,
            "source_case_sha256": self.source_case_sha256,
            "fact_freeze_at": self.fact_freeze_at,
            "question": self.question,
            "epistemic_status": self.epistemic_status,
            "execution_policy": self.execution_policy,
            "status": self.status,
            "stages": [item.to_dict() for item in self.stages],
        }


@dataclass(frozen=True)
class SimulationArtifact:
    """Case-ready MiroFish output that can never masquerade as a fact."""

    id: str
    title: str
    question: str
    fact_freeze_at: str
    mirofish_version: str
    summary: str
    signals: tuple[str, ...]
    limitations: tuple[str, ...]
    epistemic_status: str = field(default="simulation", init=False)
    artifact_kind: str = field(default="hypothesis", init=False)

    def __post_init__(self) -> None:
        for field_name in (
            "id",
            "title",
            "question",
            "fact_freeze_at",
            "mirofish_version",
            "summary",
        ):
            object.__setattr__(
                self, field_name, _require_text(getattr(self, field_name), field_name)
            )
        if not isinstance(self.signals, (list, tuple)):
            raise IntegrationContractError("signals must be a list or tuple")
        if not isinstance(self.limitations, (list, tuple)):
            raise IntegrationContractError("limitations must be a list or tuple")
        object.__setattr__(self, "signals", tuple(_require_text(item, "signals[]") for item in self.signals))
        object.__setattr__(
            self, "limitations", tuple(_require_text(item, "limitations[]") for item in self.limitations)
        )
        if not self.limitations:
            raise IntegrationContractError("simulation artifact requires limitations")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SimulationArtifact":
        supplied_status = value.get("epistemic_status", "simulation")
        if supplied_status != "simulation":
            raise IntegrationContractError(
                "MiroFish output epistemic_status must remain simulation"
            )
        supplied_kind = value.get("artifact_kind", "hypothesis")
        if supplied_kind != "hypothesis":
            raise IntegrationContractError("MiroFish output artifact_kind must be hypothesis")
        signals = value.get("signals", ())
        limitations = value.get("limitations", ())
        if not isinstance(signals, (list, tuple)):
            raise IntegrationContractError("signals must be a list or tuple")
        if not isinstance(limitations, (list, tuple)):
            raise IntegrationContractError("limitations must be a list or tuple")
        return cls(
            id=value.get("id"),
            title=value.get("title"),
            question=value.get("question"),
            fact_freeze_at=value.get("fact_freeze_at"),
            mirofish_version=value.get("mirofish_version"),
            summary=value.get("summary"),
            signals=tuple(signals),
            limitations=tuple(limitations),
        )

    def to_case_scenario(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "question": self.question,
            "epistemic_status": self.epistemic_status,
            "artifact_kind": self.artifact_kind,
            "fact_freeze_at": self.fact_freeze_at,
            "mirofish_version": self.mirofish_version,
            "summary": self.summary,
            "signals": list(self.signals),
            "limitations": list(self.limitations),
        }


class MiroFishAdapter:
    """Local facade; execution remains an explicit Leader/executor decision."""

    provider = "mirofish"

    @staticmethod
    def load_seed(
        location: str | Path,
        *,
        frozen_case: str | Path | Mapping[str, Any] | None = None,
        expected_subject: str | None = None,
        expected_fact_freeze_at: str | None = None,
        expected_case_sha256: str | None = None,
        expected_seed_sha256: str | None = None,
    ) -> SeedPackage:
        return load_seed_package(
            location,
            frozen_case=frozen_case,
            expected_subject=expected_subject,
            expected_fact_freeze_at=expected_fact_freeze_at,
            expected_case_sha256=expected_case_sha256,
            expected_seed_sha256=expected_seed_sha256,
        )

    @staticmethod
    def create_plan(package: SeedPackage) -> SimulationPlan:
        return SimulationPlan.from_seed(package)

    @staticmethod
    def normalize_artifact(value: Mapping[str, Any]) -> SimulationArtifact:
        return SimulationArtifact.from_mapping(value)


__all__ = [
    "ALLOWED_SEED_CLAIM_STATUSES",
    "MIROFISH_STAGE_DEFINITIONS",
    "MiroFishAdapter",
    "SEED_SCHEMA_VERSION",
    "SeedPackage",
    "SimulationArtifact",
    "SimulationPlan",
    "SimulationStage",
    "StageDefinition",
    "StageProgress",
    "StageRequirement",
    "StageStatus",
    "load_seed_package",
]
