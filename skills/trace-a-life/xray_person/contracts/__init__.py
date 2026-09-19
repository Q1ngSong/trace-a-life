"""Stable data contracts shared by independently owned modules."""

from .handoff import ArtifactRef, GateResult, Handoff, file_sha256, read_handoff, write_handoff
from .stages import PipelineStage, can_transition

__all__ = [
    "ArtifactRef",
    "GateResult",
    "Handoff",
    "PipelineStage",
    "can_transition",
    "file_sha256",
    "read_handoff",
    "write_handoff",
]
