"""Leader utilities for accepting hash-addressed stage artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..contracts import ArtifactRef, GateResult, Handoff, PipelineStage, write_handoff
from .state import PipelineRun, save_run


_STAGE_NUMBER = {
    PipelineStage.INTAKE.value: 1,
    PipelineStage.QUERY_PLAN.value: 2,
    PipelineStage.COLLECTION.value: 3,
    PipelineStage.VERIFICATION.value: 4,
    PipelineStage.ANALYSIS.value: 5,
    PipelineStage.FACT_FREEZE.value: 6,
    PipelineStage.SIMULATION.value: 7,
    PipelineStage.PRESENTATION.value: 8,
    PipelineStage.DELIVERY.value: 9,
}


@dataclass
class LeaderOrchestrator:
    """Persist one supervised run inside a case directory."""

    case_dir: Path
    run: PipelineRun

    @classmethod
    def create(cls, case_dir: str | Path, *, case_id: str | None = None) -> "LeaderOrchestrator":
        root = Path(case_dir).resolve()
        if not (root / "case.json").is_file():
            raise ValueError(f"case.json is missing in {root}")
        for child in (
            "research",
            "analysis",
            "simulation",
            "site",
            "delivery",
            "pipeline/handoffs",
        ):
            (root / child).mkdir(parents=True, exist_ok=True)
        run = PipelineRun(case_id=case_id or root.name)
        leader = cls(root, run)
        leader.save()
        return leader

    @property
    def run_path(self) -> Path:
        return self.case_dir / "pipeline" / "run.json"

    def artifact(self, kind: str, path: str | Path) -> ArtifactRef:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.case_dir / candidate
        return ArtifactRef.from_file(kind=kind, case_dir=self.case_dir, path=candidate)

    def accept_stage(
        self,
        stage: PipelineStage | str,
        *,
        producer: str,
        inputs: Iterable[ArtifactRef] = (),
        outputs: Iterable[ArtifactRef],
        gates: Iterable[GateResult],
        unresolved: Iterable[str] = (),
        notes: Iterable[str] = (),
    ) -> Path:
        stage_name = PipelineStage(stage).value
        handoff = Handoff(
            run_id=self.run.run_id,
            case_id=self.run.case_id,
            stage=stage_name,
            producer=producer,
            consumer="leader-agent",
            status="ready",
            inputs=tuple(inputs),
            outputs=tuple(outputs),
            gates=tuple(gates),
            unresolved=tuple(unresolved),
            notes=tuple(notes),
        )
        issues = handoff.verify_artifacts(self.case_dir)
        if issues:
            raise ValueError("; ".join(issues))
        handoff_path = (
            self.case_dir
            / "pipeline"
            / "handoffs"
            / self.run.run_id
            / f"{_STAGE_NUMBER[stage_name]:02d}-{stage_name}.json"
        )
        write_handoff(handoff, handoff_path)
        relative = handoff_path.relative_to(self.case_dir).as_posix()
        self.run.accept(handoff, relative)
        self.save()
        return handoff_path

    def block_stage(
        self,
        stage: PipelineStage | str,
        *,
        producer: str,
        unresolved: Iterable[str],
        inputs: Iterable[ArtifactRef] = (),
        outputs: Iterable[ArtifactRef] = (),
        gates: Iterable[GateResult] = (),
        notes: Iterable[str] = (),
    ) -> Path:
        """Record a reproducible stop without pretending the stage completed."""

        stage_name = PipelineStage(stage).value
        handoff = Handoff(
            run_id=self.run.run_id,
            case_id=self.run.case_id,
            stage=stage_name,
            producer=producer,
            consumer="leader-agent",
            status="blocked",
            inputs=tuple(inputs),
            outputs=tuple(outputs),
            gates=tuple(gates),
            unresolved=tuple(unresolved),
            notes=tuple(notes),
        )
        issues = handoff.verify_artifacts(self.case_dir)
        if issues:
            raise ValueError("; ".join(issues))
        handoff_path = (
            self.case_dir
            / "pipeline"
            / "handoffs"
            / self.run.run_id
            / f"{_STAGE_NUMBER[stage_name]:02d}-{stage_name}.json"
        )
        write_handoff(handoff, handoff_path)
        relative = handoff_path.relative_to(self.case_dir).as_posix()
        self.run.accept(handoff, relative)
        self.save()
        return handoff_path

    def save(self) -> None:
        save_run(self.run, self.run_path)
