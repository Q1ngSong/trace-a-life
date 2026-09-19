#!/usr/bin/env python3
"""Leader-supervised offline replay from case.json to auditable webpage delivery."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from render_case import render
from validate_case import validate
from validate_interview_analysis import validate_interview_analysis

from xray_person.contracts import GateResult, PipelineStage
from xray_person.delivery import (
    build_delivery_manifest,
    run_delivery_qa,
    write_delivery_manifest,
)
from xray_person.domain import load_case
from xray_person.integrations import MiroFishAdapter
from xray_person.orchestration import LeaderOrchestrator
from xray_person.presentation import attest_html, build_view_model, write_view_model
from xray_person.research import build_query_plan, evaluate_collection_coverage, verify_case


class PipelineBlocked(RuntimeError):
    """Raised after Leader has persisted a structured blocked handoff."""


def _write_json(path: Path, value: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _collection_plan(case_data: Mapping[str, Any], query_plan: Any) -> dict[str, Any]:
    """Create host-web collection work without claiming execution."""

    backend = str(case_data.get("provenance", {}).get("collection_backend", "host_web")).strip()
    if backend in {"", "unconfigured", "unavailable", "host_web_fallback"}:
        backend = "host_web"
    queries = [
        {
            "query_id": query.id,
            "round": query.round.value,
            "lane": query.lane_id,
            "lane_label": query.lane_label,
            "query": query.query,
            "purpose": query.purpose,
        }
        for query in query_plan.queries
    ]
    return {
        "schema_version": "xray-web-collection-plan/1",
        "backend": backend,
        "execution_status": "planned-not-executed",
        "existing_source_count": len(case_data.get("sources", [])),
        "queries": queries,
        "required_capture_fields": [
            "source_id",
            "url",
            "title",
            "published_at_or_unknown",
            "accessed_at",
            "content_or_contextual_excerpt",
            "capture_method",
            "source_ref",
        ],
        "receipts": [],
        "note": "This is a worklist only. A host must perform search/open actions and save source captures before coverage can pass.",
    }


def _block(
    leader: LeaderOrchestrator,
    stage: PipelineStage,
    producer: str,
    reasons: list[str],
    *,
    inputs: tuple[Any, ...] = (),
    outputs: tuple[Any, ...] = (),
) -> None:
    leader.block_stage(
        stage,
        producer=producer,
        inputs=inputs,
        outputs=outputs,
        gates=(GateResult(f"{stage.value}-gate", False, f"{len(reasons)} blocker(s)"),),
        unresolved=reasons,
    )
    raise PipelineBlocked("; ".join(reasons))


def run(case_location: Path, *, skip_simulation: bool = False) -> LeaderOrchestrator:
    case_path = case_location if case_location.name == "case.json" else case_location / "case.json"
    case_path = case_path.resolve()
    case_dir = case_path.parent
    case_document = load_case(case_path)
    case_data = case_document.as_dict()
    leader = LeaderOrchestrator.create(case_dir)
    case_ref = leader.artifact("case", case_path)

    subject = case_data.get("subject", {})
    anchor = str(subject.get("anchor", "")).strip()
    if not anchor:
        _block(leader, PipelineStage.INTAKE, "intake-agent", ["subject.anchor is required"])
    leader.accept_stage(
        PipelineStage.INTAKE,
        producer="intake-agent",
        outputs=(case_ref,),
        gates=(GateResult("identity-anchor", True, anchor),),
    )

    query_plan = build_query_plan(case_document)
    query_path = _write_json(case_dir / "research" / "query-plan.json", query_plan.as_dict())
    query_ref = leader.artifact("query-plan", query_path)
    leader.accept_stage(
        PipelineStage.QUERY_PLAN,
        producer="research-agent",
        inputs=(case_ref,),
        outputs=(query_ref,),
        gates=(
            GateResult("seven-research-lanes", len(query_plan.queries) == 21, "21 queries"),
            GateResult(
                "identity-anchor-in-query",
                all(
                    all(identity_anchor in item.query for identity_anchor in query_plan.identity_anchors)
                    for item in query_plan.queries
                ),
                ", ".join(query_plan.identity_anchors),
            ),
        ),
    )

    collection_path = _write_json(
        case_dir / "research" / "collection-plan.json",
        _collection_plan(case_data, query_plan),
    )
    collection_ref = leader.artifact("collection-plan", collection_path)
    sources = case_data.get("sources", [])
    backend = str(case_data.get("provenance", {}).get("collection_backend", ""))
    if not sources or backend in {"", "unconfigured", "unavailable"}:
        _block(
            leader,
            PipelineStage.COLLECTION,
            "collection-agent",
            ["no pre-collected evidence is available; execute the planned collection work and ingest receipts"],
            inputs=(case_ref, query_ref),
            outputs=(collection_ref,),
        )
    collection_coverage = evaluate_collection_coverage(
        case_document,
        collection_dir=case_dir / "research" / "collection",
    )
    collection_coverage_path = _write_json(
        case_dir / "research" / "collection-coverage.json",
        collection_coverage,
    )
    collection_coverage_ref = leader.artifact("collection-coverage", collection_coverage_path)
    if not collection_coverage["ready"]:
        _block(
            leader,
            PipelineStage.COLLECTION,
            "collection-agent",
            list(collection_coverage["blockers"]),
            inputs=(case_ref, query_ref),
            outputs=(collection_ref, collection_coverage_ref),
        )
    leader.accept_stage(
        PipelineStage.COLLECTION,
        producer="collection-agent",
        inputs=(case_ref, query_ref),
        outputs=(collection_ref, collection_coverage_ref),
        gates=(
            GateResult("precollected-sources-present", True, f"{len(sources)} source(s)"),
            GateResult("collection-coverage-complete", True, "all referenced evidence is collected and aligned"),
        ),
        notes=("Existing collection receipts and source captures are required before verification and analysis.",),
    )

    interview_validation = validate_interview_analysis(case_path)
    if not interview_validation.ready:
        _block(
            leader,
            PipelineStage.VERIFICATION,
            "interview-evidence-agent",
            list(interview_validation.errors),
            inputs=(case_ref, collection_ref),
        )

    verification = verify_case(case_document)
    verification_payload = verification.to_dict()
    verification_path = _write_json(
        case_dir / "research" / "verification-report.json", verification_payload
    )
    verification_ref = leader.artifact("verification-report", verification_path)
    if not verification.stop_gate.ready:
        _block(
            leader,
            PipelineStage.VERIFICATION,
            "evidence-agent",
            list(verification.stop_gate.blockers),
            inputs=(case_ref, collection_ref),
            outputs=(verification_ref,),
        )
    leader.accept_stage(
        PipelineStage.VERIFICATION,
        producer="evidence-agent",
        inputs=(case_ref, collection_ref),
        outputs=(verification_ref,),
        gates=(
            GateResult("five-gate-interview-analysis", True, "all full transcripts audited"),
            GateResult("three-round-audit", True, ", ".join(verification.stop_gate.completed_rounds)),
            GateResult("evidence-stop-gate", True, f"{len(verification.claims)} claim(s)"),
        ),
        unresolved=tuple(verification.stop_gate.warnings),
    )

    view_model = build_view_model(case_data)
    analysis_path = write_view_model(case_dir / "analysis" / "view.json", view_model)
    analysis_ref = leader.artifact("analysis-view", analysis_path)
    leader.accept_stage(
        PipelineStage.ANALYSIS,
        producer="analysis-agent",
        inputs=(case_ref, verification_ref),
        outputs=(analysis_ref,),
        gates=(
            GateResult("decision-projection", True, f"{len(view_model['fact']['decisions'])} decision(s)"),
            GateResult("simulation-separated", True, "fact and simulation are separate top-level layers"),
        ),
    )

    validation = validate(case_data, strict=True)
    freeze = case_data.get("fact_freeze", {})
    freeze_errors = list(validation.errors)
    if freeze.get("status") != "frozen" or not freeze.get("frozen_at"):
        freeze_errors.append("case must already have fact_freeze.status=frozen and frozen_at")
    if freeze_errors:
        _block(
            leader,
            PipelineStage.FACT_FREEZE,
            "leader-agent",
            freeze_errors,
            inputs=(case_ref, verification_ref, analysis_ref),
            outputs=(case_ref,),
        )
    leader.accept_stage(
        PipelineStage.FACT_FREEZE,
        producer="leader-agent",
        inputs=(verification_ref, analysis_ref),
        outputs=(case_ref,),
        gates=(
            GateResult("strict-case-validation", True, "0 errors"),
            GateResult("fact-freeze-present", True, str(freeze.get("frozen_at"))),
        ),
    )

    simulation_ref = None
    simulation_manifest = case_dir / "simulation" / "simulation-manifest.json"
    scenarios = case_data.get("scenarios", [])
    if skip_simulation or (not simulation_manifest.is_file() and not scenarios):
        leader.run.skip_simulation("explicitly skipped" if skip_simulation else "no simulation package")
        leader.save()
    else:
        simulation_errors: list[str] = []
        plan = None
        try:
            package = MiroFishAdapter.load_seed(
                case_dir / "simulation", frozen_case=case_path
            )
            plan = MiroFishAdapter.create_plan(package)
            if str(package.manifest.get("fact_freeze_at")) != str(freeze.get("frozen_at")):
                simulation_errors.append("seed fact_freeze_at does not match the current case")
        except Exception as error:  # integration errors are persisted as stage blockers
            simulation_errors.append(str(error))
        normalized = []
        for scenario in scenarios:
            try:
                artifact = MiroFishAdapter.normalize_artifact(scenario)
                if artifact.fact_freeze_at != str(freeze.get("frozen_at")):
                    simulation_errors.append(f"scenario {artifact.id} uses a stale fact freeze")
                normalized.append(artifact.to_case_scenario())
            except Exception as error:
                simulation_errors.append(str(error))
        run_state_path = _write_json(
            case_dir / "simulation" / "run-state.json",
            {
                "schema_version": "xray-mirofish-run-state/1",
                "execution_status": "not-executed-by-leader-replay",
                "plan": plan.to_dict() if plan else None,
                "normalized_existing_scenarios": normalized,
                "note": "This replay validates existing simulation artifacts; it does not execute MiroFish.",
            },
        )
        simulation_ref = leader.artifact("simulation-run-state", run_state_path)
        if simulation_errors:
            _block(
                leader,
                PipelineStage.SIMULATION,
                "mirofish-agent",
                simulation_errors,
                inputs=(case_ref,),
                outputs=(simulation_ref,),
            )
        leader.run.skip_simulation(
            "reported-only replay: existing seed and scenarios validated; MiroFish was not executed"
        )
        leader.save()

    site_view_path = write_view_model(case_dir / "site" / "view-model.json", view_model)
    html = attest_html(render(case_data), case_data, view_model)
    html_path = case_dir / "site" / "index.html"
    html_path.write_text(html, encoding="utf-8")
    site_view_ref = leader.artifact("page-view-model", site_view_path)
    html_ref = leader.artifact("html", html_path)
    presentation_inputs = [case_ref, analysis_ref]
    if simulation_ref is not None:
        presentation_inputs.append(simulation_ref)
    leader.accept_stage(
        PipelineStage.PRESENTATION,
        producer="web-agent",
        inputs=tuple(presentation_inputs),
        outputs=(site_view_ref, html_ref),
        gates=(
            GateResult("html-attested", True, "case and view-model SHA-256 embedded"),
            GateResult("single-file-html", True, "renderer emits no CDN dependency"),
        ),
    )

    manifest = build_delivery_manifest(
        case_path=case_path,
        view_model_path=site_view_path,
        html_path=html_path,
        simulation_path=case_dir / "simulation" if simulation_ref is not None else None,
        base_dir=case_dir,
    )
    manifest_path = write_delivery_manifest(case_dir / "delivery" / "manifest.json", manifest)
    qa = run_delivery_qa(
        case_data=case_data,
        view_model=view_model,
        html=html,
        manifest=manifest,
        manifest_base_dir=case_dir,
    )
    qa_path = _write_json(case_dir / "delivery" / "qa-report.json", qa.to_dict())
    manifest_ref = leader.artifact("delivery-manifest", manifest_path)
    qa_ref = leader.artifact("qa-report", qa_path)
    if not qa.ok:
        _block(
            leader,
            PipelineStage.DELIVERY,
            "qa-agent",
            [f"{issue.code}: {issue.message}" for issue in qa.errors],
            inputs=(case_ref, site_view_ref, html_ref),
            outputs=(manifest_ref, qa_ref),
        )
    leader.accept_stage(
        PipelineStage.DELIVERY,
        producer="qa-agent",
        inputs=(case_ref, site_view_ref, html_ref),
        outputs=(manifest_ref, qa_ref),
        gates=(
            GateResult("delivery-qa", True, "0 errors"),
            GateResult("manifest-current", True, "all registered hashes match"),
        ),
        unresolved=tuple(f"warning: {issue.code}: {issue.message}" for issue in qa.warnings),
    )
    return leader


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path, help="case directory or case.json")
    parser.add_argument(
        "--skip-simulation",
        action="store_true",
        help="do not validate or include a simulation package",
    )
    args = parser.parse_args()
    try:
        leader = run(args.case, skip_simulation=args.skip_simulation)
    except PipelineBlocked as error:
        print(f"BLOCKED: {error}")
        return 2
    print(leader.run_path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
