"""Persistent Leader state for a single case run."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..contracts import Handoff, PipelineStage, can_transition


RUN_SCHEMA = "xray-run/1"
STAGE_STATUSES = frozenset({"pending", "in_progress", "accepted", "blocked", "rejected", "skipped"})


@dataclass
class StageRecord:
    stage: str
    owner: str
    status: str = "pending"
    handoff_path: str = ""
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    detail: str = ""

    def __post_init__(self) -> None:
        PipelineStage(self.stage)
        if self.status not in STAGE_STATUSES:
            raise ValueError(f"invalid stage status: {self.status}")
        if not self.owner.strip():
            raise ValueError("stage owner is required")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "StageRecord":
        return cls(**value)


DEFAULT_OWNERS = {
    PipelineStage.INTAKE.value: "intake-agent",
    PipelineStage.QUERY_PLAN.value: "research-agent",
    PipelineStage.COLLECTION.value: "collection-agent",
    PipelineStage.VERIFICATION.value: "evidence-agent",
    PipelineStage.ANALYSIS.value: "analysis-agent",
    PipelineStage.FACT_FREEZE.value: "leader-agent",
    PipelineStage.SIMULATION.value: "mirofish-agent",
    PipelineStage.PRESENTATION.value: "web-agent",
    PipelineStage.DELIVERY.value: "qa-agent",
}

_PREREQUISITES: dict[str, tuple[tuple[str, ...], ...]] = {
    PipelineStage.INTAKE.value: (),
    PipelineStage.QUERY_PLAN.value: ((PipelineStage.INTAKE.value,),),
    PipelineStage.COLLECTION.value: ((PipelineStage.QUERY_PLAN.value,),),
    PipelineStage.VERIFICATION.value: ((PipelineStage.COLLECTION.value,),),
    PipelineStage.ANALYSIS.value: ((PipelineStage.VERIFICATION.value,),),
    PipelineStage.FACT_FREEZE.value: ((PipelineStage.ANALYSIS.value,),),
    PipelineStage.SIMULATION.value: ((PipelineStage.FACT_FREEZE.value,),),
    PipelineStage.PRESENTATION.value: (
        (PipelineStage.FACT_FREEZE.value,),
        (PipelineStage.FACT_FREEZE.value, PipelineStage.SIMULATION.value),
    ),
    PipelineStage.DELIVERY.value: ((PipelineStage.PRESENTATION.value,),),
}


@dataclass
class PipelineRun:
    case_id: str
    run_id: str = field(default_factory=lambda: f"run-{uuid4().hex[:12]}")
    schema_version: str = RUN_SCHEMA
    current_stage: str = PipelineStage.INTAKE.value
    stages: dict[str, StageRecord] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        if self.schema_version != RUN_SCHEMA:
            raise ValueError(f"unsupported run schema: {self.schema_version}")
        if not self.case_id.strip():
            raise ValueError("case_id is required")
        PipelineStage(self.current_stage)
        if not self.stages:
            self.stages = {name: StageRecord(stage=name, owner=owner) for name, owner in DEFAULT_OWNERS.items()}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PipelineRun":
        payload = dict(value)
        payload["stages"] = {name: StageRecord.from_dict(record) for name, record in value.get("stages", {}).items()}
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def start(self, stage: PipelineStage | str) -> None:
        stage_name = PipelineStage(stage).value
        record = self.stages[stage_name]
        if record.status not in {"pending", "blocked", "rejected"}:
            raise ValueError(f"stage {stage_name} cannot start from {record.status}")
        record.status = "in_progress"
        record.updated_at = datetime.now(timezone.utc).isoformat()
        self.current_stage = stage_name
        self.updated_at = record.updated_at

    def accept(self, handoff: Handoff, handoff_path: str) -> None:
        if handoff.run_id != self.run_id or handoff.case_id != self.case_id:
            raise ValueError("handoff does not belong to this run")
        stage_name = PipelineStage(handoff.stage).value
        record = self.stages[stage_name]
        if handoff.status == "ready" and not self._prerequisites_satisfied(stage_name):
            raise ValueError(f"prerequisites are not accepted for stage {stage_name}")
        if record.owner != handoff.producer and handoff.producer != "leader-agent":
            raise ValueError(f"unexpected producer for {stage_name}: {handoff.producer}")
        record.status = "accepted" if handoff.status == "ready" else handoff.status
        record.handoff_path = handoff_path
        record.detail = "; ".join(handoff.unresolved or handoff.notes)
        record.updated_at = datetime.now(timezone.utc).isoformat()
        self.updated_at = record.updated_at
        if handoff.status == "ready":
            next_candidates = [candidate for candidate in PipelineStage if can_transition(stage_name, candidate.value)]
            if next_candidates:
                self.current_stage = next_candidates[0].value

    def _prerequisites_satisfied(self, stage_name: str) -> bool:
        alternatives = _PREREQUISITES[stage_name]
        if not alternatives:
            return True
        for group in alternatives:
            if all(
                self.stages[name].status in {"accepted", "skipped"}
                for name in group
            ):
                return True
        return False

    def skip_simulation(self, detail: str = "not requested") -> None:
        if self.stages[PipelineStage.FACT_FREEZE.value].status != "accepted":
            raise ValueError("simulation can only be skipped after fact_freeze is accepted")
        record = self.stages[PipelineStage.SIMULATION.value]
        record.status = "skipped"
        record.detail = detail
        record.updated_at = datetime.now(timezone.utc).isoformat()
        self.updated_at = record.updated_at
        self.current_stage = PipelineStage.PRESENTATION.value


def save_run(run: PipelineRun, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(run.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_run(path: Path) -> PipelineRun:
    return PipelineRun.from_dict(json.loads(path.read_text(encoding="utf-8")))
