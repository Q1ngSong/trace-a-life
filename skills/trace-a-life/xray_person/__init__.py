"""Auditable orchestration package for the Trace a Life skill."""

from .contracts import ArtifactRef, GateResult, Handoff, PipelineStage, file_sha256
from .analysis import ANALYSIS_SCHEMA_VERSION, build_analysis_proposal
from .delivery import build_delivery_manifest, run_delivery_qa
from .domain import CaseDocument, CaseFormatError, load_case
from .integrations import MiroFishAdapter
from .orchestration import LeaderOrchestrator, PipelineRun
from .presentation import attest_html, build_view_model
from .research import RoundCompletion, build_query_plan, verify_case

__all__ = [
    "ArtifactRef",
    "ANALYSIS_SCHEMA_VERSION",
    "CaseDocument",
    "CaseFormatError",
    "GateResult",
    "Handoff",
    "LeaderOrchestrator",
    "MiroFishAdapter",
    "PipelineRun",
    "PipelineStage",
    "RoundCompletion",
    "attest_html",
    "build_delivery_manifest",
    "build_analysis_proposal",
    "build_query_plan",
    "build_view_model",
    "file_sha256",
    "load_case",
    "run_delivery_qa",
    "verify_case",
]

__version__ = "0.1.0"
