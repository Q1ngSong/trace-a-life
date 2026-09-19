"""Pipeline stage names and legal Leader-controlled transitions."""

from __future__ import annotations

from enum import Enum


class PipelineStage(str, Enum):
    INTAKE = "intake"
    QUERY_PLAN = "query_plan"
    COLLECTION = "collection"
    VERIFICATION = "verification"
    ANALYSIS = "analysis"
    FACT_FREEZE = "fact_freeze"
    SIMULATION = "simulation"
    PRESENTATION = "presentation"
    DELIVERY = "delivery"


_NEXT: dict[PipelineStage, frozenset[PipelineStage]] = {
    PipelineStage.INTAKE: frozenset({PipelineStage.QUERY_PLAN}),
    PipelineStage.QUERY_PLAN: frozenset({PipelineStage.COLLECTION}),
    PipelineStage.COLLECTION: frozenset({PipelineStage.VERIFICATION}),
    PipelineStage.VERIFICATION: frozenset({PipelineStage.ANALYSIS}),
    PipelineStage.ANALYSIS: frozenset({PipelineStage.FACT_FREEZE}),
    PipelineStage.FACT_FREEZE: frozenset({PipelineStage.SIMULATION, PipelineStage.PRESENTATION}),
    PipelineStage.SIMULATION: frozenset({PipelineStage.PRESENTATION}),
    PipelineStage.PRESENTATION: frozenset({PipelineStage.DELIVERY}),
    PipelineStage.DELIVERY: frozenset(),
}


def can_transition(current: PipelineStage | str, target: PipelineStage | str) -> bool:
    """Return whether Leader may accept ``target`` directly after ``current``."""

    current_stage = PipelineStage(current)
    target_stage = PipelineStage(target)
    return target_stage in _NEXT[current_stage]
