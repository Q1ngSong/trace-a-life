from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SKILL_ROOT.parents[1]
sys.path.insert(0, str(SKILL_ROOT))

from xray_person.integrations import (  # noqa: E402
    ExecutorAttestation,
    IntegrationContractError,
    MIROFISH_STAGE_DEFINITIONS,
    MiroFishAdapter,
    SimulationArtifact,
    SimulationPlan,
    SimulationStage,
    StageStatus,
    ToolCall,
    ToolResult,
    normalize_receipt,
)


class ContractTests(unittest.TestCase):
    def test_tool_call_is_deterministic_and_deeply_immutable(self) -> None:
        source = {"queries": ["founder filing"]}
        first = ToolCall.create(
            provider="host-web",
            action="search",
            tool_name="host_web_search",
            arguments=source,
        )
        source["queries"].append("mutated later")
        second = ToolCall.create(
            provider="host-web",
            action="search",
            tool_name="host_web_search",
            arguments={"queries": ["founder filing"]},
        )
        self.assertEqual(first.call_id, second.call_id)
        self.assertEqual(first.to_dict()["arguments"], {"queries": ["founder filing"]})
        with self.assertRaises(TypeError):
            first.arguments["new"] = "value"  # type: ignore[index]

    def test_failed_result_requires_error(self) -> None:
        with self.assertRaises(IntegrationContractError):
            ToolResult("call", "provider", "tool", False)

    def test_collection_receipt_requires_attestation_for_completed_result(self) -> None:
        call = ToolCall.create(
            provider="host-web",
            action="capture",
            tool_name="host_web_capture",
            arguments={"url": "https://example.com"},
        )
        reported = normalize_receipt(call, ToolResult.success(call, {"title": "结果"}))
        self.assertEqual("reported-only", reported.status)
        payload = {"source_id": "src-1", "title": "结果"}
        attestation = ExecutorAttestation.create(
            call,
            payload,
            executor="test-host",
            attempt_id="attempt-1",
            transport="browser",
            executed_at="2026-09-19T10:00:00+08:00",
            status_code=200,
            server_version="host-web-test",
        )
        completed = normalize_receipt(
            call, ToolResult.attested_success(call, payload, attestation)
        )
        self.assertEqual("completed", completed.status)
        self.assertEqual(("src-1",), completed.resource_ids)

class MiroFishAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.example_package = PROJECT_ROOT / "examples" / "fictional-founder" / "simulation"
        self.example_case = PROJECT_ROOT / "examples" / "fictional-founder" / "case.json"

    def load_verified_plan(self) -> SimulationPlan:
        package = MiroFishAdapter.load_seed(
            self.example_package, frozen_case=self.example_case
        )
        return MiroFishAdapter.create_plan(package)

    def test_load_existing_seed_package_and_create_explicit_plan(self) -> None:
        package = MiroFishAdapter.load_seed(
            self.example_package, frozen_case=self.example_case
        )
        plan = MiroFishAdapter.create_plan(package)
        self.assertEqual(package.epistemic_status, "simulation-input")
        self.assertEqual(plan.epistemic_status, "simulation")
        self.assertEqual(plan.execution_policy, "explicit-executor-required")
        self.assertEqual(plan.current.definition.stage, SimulationStage.ONTOLOGY_GENERATION)
        self.assertEqual(plan.current.status, StageStatus.READY)
        self.assertEqual(len(plan.stages), 11)

    def test_stage_definitions_match_vendored_multistage_routes(self) -> None:
        routes = [(item.method, item.endpoint_template) for item in MIROFISH_STAGE_DEFINITIONS]
        self.assertEqual(
            routes,
            [
                ("POST", "/api/graph/ontology/generate"),
                ("POST", "/api/graph/build"),
                ("GET", "/api/graph/task/{task_id}"),
                ("POST", "/api/simulation/create"),
                ("POST", "/api/simulation/prepare"),
                ("POST", "/api/simulation/prepare/status"),
                ("POST", "/api/simulation/start"),
                ("GET", "/api/simulation/{simulation_id}/run-status"),
                ("POST", "/api/report/generate"),
                ("POST", "/api/report/generate/status"),
                ("GET", "/api/report/{report_id}"),
            ],
        )
        self.assertTrue(
            all(item.output_epistemic_status == "simulation" for item in MIROFISH_STAGE_DEFINITIONS)
        )

    def test_state_machine_forbids_skipping_and_unlocks_one_stage_at_a_time(self) -> None:
        plan = self.load_verified_plan()
        with self.assertRaises(IntegrationContractError):
            plan.start(SimulationStage.GRAPH_BUILD)
        running = plan.start(SimulationStage.ONTOLOGY_GENERATION)
        self.assertEqual(plan.current.status, StageStatus.READY)
        self.assertEqual(running.current.status, StageStatus.RUNNING)
        request = {"seed_package_sha256": plan.seed_package_sha256}
        response = {"success": True, "data": {"project_id": "proj-1"}}
        attestation = running.attest_stage_execution(
            SimulationStage.ONTOLOGY_GENERATION,
            request=request,
            response=response,
            executor="test-http-executor",
            attempt_id="attempt-ontology-1",
            transport="http",
            executed_at="2026-08-31T12:00:00+08:00",
            status_code=200,
            server_version="mirofish-test-commit",
        )
        advanced = running.complete(
            SimulationStage.ONTOLOGY_GENERATION,
            request=request,
            response=response,
            attestation=attestation,
            output_refs=("project:proj-1",),
        )
        self.assertEqual(advanced.stages[0].status, StageStatus.COMPLETED)
        self.assertEqual(advanced.current.definition.stage, SimulationStage.GRAPH_BUILD)
        self.assertEqual(advanced.current.status, StageStatus.READY)

    def test_failed_stage_is_terminal_for_the_plan(self) -> None:
        plan = self.load_verified_plan()
        failed = plan.start(SimulationStage.ONTOLOGY_GENERATION).fail(
            SimulationStage.ONTOLOGY_GENERATION, error="executor failed"
        )
        self.assertEqual(failed.status, "failed")
        self.assertIsNone(failed.current)
        self.assertEqual(failed.stages[1].status, StageStatus.BLOCKED)

    def test_plan_rejects_unbound_seed_and_unattested_completion(self) -> None:
        unbound = MiroFishAdapter.load_seed(self.example_package)
        self.assertEqual("unverified", unbound.binding_status)
        with self.assertRaises(IntegrationContractError):
            MiroFishAdapter.create_plan(unbound)

        running = self.load_verified_plan().start(SimulationStage.ONTOLOGY_GENERATION)
        request = {"seed_package_sha256": running.seed_package_sha256}
        response = {"success": True, "data": {"project_id": "proj-1"}}
        with self.assertRaises(IntegrationContractError):
            running.complete(
                SimulationStage.ONTOLOGY_GENERATION,
                request=request,
                response=response,
                attestation=None,  # type: ignore[arg-type]
                output_refs=("project:proj-1",),
            )

    def test_seed_rejects_non_evidence_claim_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "seed.md").write_text("frozen seed", encoding="utf-8")
            manifest = {
                "schema_version": "x-ray-mirofish-seed/1",
                "subject": "Example",
                "question": "What if?",
                "fact_freeze_at": "2026-08-31T00:00:00Z",
                "seed_file": "seed.md",
                "epistemic_status": "simulation-input",
                "allowed_claim_statuses": ["verified", "unverified"],
            }
            (root / "simulation-manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            with self.assertRaises(IntegrationContractError):
                MiroFishAdapter.load_seed(root)

    def test_simulation_artifact_cannot_be_promoted_to_fact(self) -> None:
        base = {
            "id": "scenario-1",
            "title": "Alternative path",
            "question": "What if?",
            "fact_freeze_at": "2026-08-31T00:00:00Z",
            "mirofish_version": "vendored-commit",
            "summary": "A model-generated possibility.",
            "signals": ["observable signal"],
            "limitations": ["depends on the seed assumptions"],
        }
        artifact = SimulationArtifact.from_mapping(base)
        self.assertEqual(artifact.to_case_scenario()["epistemic_status"], "simulation")
        self.assertEqual(artifact.to_case_scenario()["artifact_kind"], "hypothesis")
        with self.assertRaises(IntegrationContractError):
            SimulationArtifact.from_mapping({**base, "epistemic_status": "fact"})


if __name__ == "__main__":
    unittest.main()
