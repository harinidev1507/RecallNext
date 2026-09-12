import csv
import json
import shutil
from pathlib import Path

import pytest

from backend.services.recall_workflow import (
    ConflictError,
    RecallWorkflow,
    default_workflow,
)
from planner.allocation_bounds import classify_shipments

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "adversarial_cases.json").read_text()
)
DATA = Path(__file__).parents[1] / "data" / "sample"


def case(name):
    selected = next(item for item in FIXTURE["scenarios"] if item["name"] == name)
    assert selected["reason"]
    return selected["expected"]


def decision(workflow, shipment_id):
    return next(
        item
        for item in workflow.decisions()["decisions"]
        if item["shipment_id"] == shipment_id
    )


def proposal(workflow, action_id, fact, content_hash):
    return workflow.submit_evidence(
        {
            "action_id": action_id,
            "source_reference": f"synthetic://{action_id.lower()}",
            "proposed_fact": fact,
            "content_hash": content_hash,
            "review_status": "PENDING_REVIEW",
        }
    )


def copied_fixture(tmp_path):
    target = tmp_path / "sample"
    shutil.copytree(DATA, target)
    return target


def test_fixture_records_every_required_scenario_and_reason():
    assert FIXTURE["synthetic"] is True
    assert FIXTURE["fixture_version"] == "qa-v2"
    assert {item["name"] for item in FIXTURE["scenarios"]} == {
        "clean_initial_incident",
        "known_mapping_reduces_scope",
        "mixed_container_single_scan",
        "contradictory_label_and_pick_log",
        "missing_source_stream",
        "duplicate_inventory_snapshot",
        "same_code_different_sources",
        "solver_timeout_or_error",
        "rejected_or_unavailable_evidence",
        "evidence_retraction",
    }
    assert all(item["reason"] and item["expected"] for item in FIXTURE["scenarios"])


def test_clean_initial_incident_matches_recorded_bounds():
    expected = case("clean_initial_incident")
    workflow = default_workflow()

    assert workflow.incident()["summary"]["status_counts"] == {
        "POSSIBLE_INCLUSION": expected["POSSIBLE_INCLUSION"],
        "EXCLUDED_UNDER_ASSUMPTIONS": expected["EXCLUDED_UNDER_ASSUMPTIONS"],
    }
    assert (
        workflow.incident()["summary"]["feasible_scenarios"]
        == expected["feasible_scenarios"]
    )


def test_known_mapping_reduces_only_supported_scope():
    expected = case("known_mapping_reduces_scope")
    workflow = default_workflow()
    fact = {
        "fact_type": "shipment_allocation",
        "shipment_id": "S-200",
        "allocations": {"FARM-A:GOOD-2026-01": 5},
    }
    evidence = proposal(workflow, "ACT-MANIFEST-S200", fact, "known-mapping-qa-v2")

    workflow.accept_evidence(evidence["evidence_id"], "QA reviewer", 1)
    actual = decision(workflow, expected["shipment_id"])

    assert actual["status"] == expected["status"]
    assert actual["min_recalled_cases"] == expected["min"]
    assert actual["max_recalled_cases"] == expected["max"]


def test_one_case_scan_does_not_clear_a_mixed_container():
    expected = case("mixed_container_single_scan")
    workflow = default_workflow()
    evidence = proposal(
        workflow,
        "ACT-SCAN-C200",
        workflow.example_fact("ACT-SCAN-C200"),
        "single-scan-qa-v2",
    )

    result = workflow.accept_evidence(evidence["evidence_id"], "QA reviewer", 1)
    actual = decision(workflow, expected["shipment_id"])

    assert result["decision_diff"]
    assert actual["status"] == expected["status"]
    assert actual["min_recalled_cases"] == expected["min"]
    assert actual["max_recalled_cases"] == expected["max"]


def test_contradictory_label_and_pick_log_become_unresolved():
    expected = case("contradictory_label_and_pick_log")
    workflow = default_workflow()
    label = proposal(
        workflow,
        "ACT-LABEL-C100",
        workflow.example_fact("ACT-LABEL-C100"),
        "label-qa-v2",
    )
    workflow.accept_evidence(label["evidence_id"], "QA reviewer", 1)
    pick = proposal(
        workflow,
        "ACT-PICK-C200",
        workflow.example_fact("ACT-PICK-C200"),
        "pick-qa-v2",
    )

    result = workflow.accept_evidence(pick["evidence_id"], "QA reviewer", 2)

    assert result["solver_status"] == expected["solver_status"]
    assert len(workflow.decisions()["decisions"]) == expected["shipment_count"]
    assert all(
        item["status"] == expected["status"]
        for item in workflow.decisions()["decisions"]
    )


def test_missing_required_source_stream_blocks_narrowing(tmp_path):
    expected = case("missing_source_stream")
    data = copied_fixture(tmp_path)
    path = data / "source_coverage.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
        fields = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(row for row in rows if row["source_system"] != "ERP_DISPATCH")

    workflow = RecallWorkflow(data)

    assert workflow.incident()["summary"]["solver_status"] == expected["solver_status"]
    assert expected["issue"] in workflow.incident()["summary"]["data_quality_issues"]
    assert all(
        item["status"] == expected["status"]
        for item in workflow.decisions()["decisions"]
    )


def test_complete_optional_source_does_not_invalidate_required_coverage(tmp_path):
    data = copied_fixture(tmp_path)
    path = data / "source_coverage.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
        fields = list(rows[0])
    rows.append(
        {
            "incident_id": "INC-DEMO-001",
            "incident_version": "1",
            "source_system": "OPTIONAL_AUDIT_FEED",
            "expected_records": "1",
            "received_records": "1",
            "is_complete": "true",
            "issue_detail": "",
        }
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    workflow = RecallWorkflow(data)
    summary = workflow.incident()["summary"]

    assert summary["solver_status"] == "SUCCESS"
    assert summary["feasible_scenarios"] == 125
    assert "MISSING_REQUIRED_SOURCE_COVERAGE" not in summary["data_quality_issues"]


def test_duplicate_inventory_snapshot_does_not_double_count(tmp_path):
    expected = case("duplicate_inventory_snapshot")
    data = copied_fixture(tmp_path)
    path = data / "shipment.csv"
    with path.open(encoding="utf-8") as handle:
        lines = handle.readlines()
    with path.open("w", encoding="utf-8") as handle:
        handle.writelines([*lines, lines[1]])

    workflow = RecallWorkflow(data)

    assert len(workflow.decisions()["decisions"]) == expected["shipment_count"]
    assert expected["issue"] in workflow.incident()["summary"]["data_quality_issues"]
    assert all(
        item["status"] == expected["status"]
        for item in workflow.decisions()["decisions"]
    )


def test_same_lot_code_from_another_source_stays_distinct():
    expected = case("same_code_different_sources")
    workflow = default_workflow()

    for shipment_id in expected["shipments"]:
        actual = decision(workflow, shipment_id)
        assert actual["status"] == expected["status"]
        assert actual["min_recalled_cases"] == expected["min"]
        assert actual["max_recalled_cases"] == expected["max"]


@pytest.mark.parametrize("solver_status", ["TIMEOUT", "ERROR"])
def test_solver_timeout_or_error_is_unresolved(solver_status):
    expected = case("solver_timeout_or_error")
    workflow = default_workflow()
    result = classify_shipments(
        workflow.lots,
        workflow.shipments,
        workflow._base_scenarios,
        workflow.recalled_lot_ids,
        {
            "candidate_universe_complete": True,
            "solver_status": solver_status,
            "inventory_balance_mode": "CLOSED",
        },
    )

    assert solver_status in expected["statuses"]
    assert all(item["status"] == expected["decision_status"] for item in result)


def test_rejected_and_unavailable_evidence_cannot_narrow_scope():
    expected = case("rejected_or_unavailable_evidence")
    workflow = default_workflow()
    before = workflow.decisions()
    evidence = proposal(
        workflow,
        "ACT-MANIFEST-S200",
        workflow.example_fact("ACT-MANIFEST-S200"),
        "rejected-qa-v2",
    )

    result = workflow.reject_evidence(
        evidence["evidence_id"], "QA reviewer", 1, "Source is unavailable"
    )

    assert result["evidence"]["status"] == expected["review_status"]
    assert workflow.current_version == expected["version"]
    assert workflow.decisions() == before
    assert all(
        action["worst_case_resolved_cases"] == expected["worst_case_resolved_cases"]
        for action in workflow.evidence_actions()["actions"]
    )


def test_retraction_invalidates_an_accepted_exclusion():
    expected = case("evidence_retraction")
    workflow = default_workflow()
    fact = {
        "fact_type": "shipment_allocation",
        "shipment_id": "S-200",
        "allocations": {"FARM-A:GOOD-2026-01": 5},
    }
    evidence = proposal(workflow, "ACT-MANIFEST-S200", fact, "retraction-qa-v2")
    workflow.accept_evidence(evidence["evidence_id"], "QA reviewer", 1)

    assert decision(workflow, "S-200")["status"] == expected["accepted_status"]
    result = workflow.retract_evidence(
        evidence["evidence_id"], "QA reviewer", 2, "Manifest was withdrawn"
    )

    assert result["evidence"]["status"] == "RETRACTED"
    assert workflow.current_version == expected["version"]
    assert decision(workflow, "S-200")["status"] == expected["retracted_status"]


def test_retracting_newest_conflict_preserves_an_older_active_conflict():
    workflow = default_workflow()
    label = proposal(
        workflow,
        "ACT-LABEL-C100",
        workflow.example_fact("ACT-LABEL-C100"),
        "older-conflict-label-qa-v2",
    )
    assert (
        workflow.accept_evidence(label["evidence_id"], "QA reviewer", 1)[
            "solver_status"
        ]
        == "SUCCESS"
    )

    older_conflict = proposal(
        workflow,
        "ACT-PICK-C200",
        workflow.example_fact("ACT-PICK-C200"),
        "older-conflict-pick-qa-v2",
    )
    assert (
        workflow.accept_evidence(older_conflict["evidence_id"], "QA reviewer", 2)[
            "solver_status"
        ]
        == "CONFLICT"
    )

    newest_conflict = proposal(
        workflow,
        "ACT-MANIFEST-S200",
        workflow.example_fact("ACT-MANIFEST-S200"),
        "newest-conflict-manifest-qa-v2",
    )
    assert (
        workflow.accept_evidence(newest_conflict["evidence_id"], "QA reviewer", 3)[
            "solver_status"
        ]
        == "CONFLICT"
    )

    result = workflow.retract_evidence(
        newest_conflict["evidence_id"],
        "QA reviewer",
        4,
        "Newest conflicting source was withdrawn",
    )
    decisions = workflow.decisions()

    assert result["solver_status"] == "CONFLICT"
    assert workflow.current_version == 5
    assert older_conflict["evidence_id"] in decisions["evidence_references"]
    assert all(item["status"] == "UNRESOLVED" for item in decisions["decisions"])


def test_pending_proposal_cannot_cross_an_incident_version():
    workflow = default_workflow()
    first = proposal(
        workflow,
        "ACT-MANIFEST-S200",
        workflow.example_fact("ACT-MANIFEST-S200"),
        "first-version-qa-v2",
    )
    stale = proposal(
        workflow,
        "ACT-SCAN-C200",
        workflow.example_fact("ACT-SCAN-C200"),
        "stale-version-qa-v2",
    )
    workflow.accept_evidence(first["evidence_id"], "QA reviewer", 1)

    with pytest.raises(ConflictError, match="earlier incident version"):
        workflow.accept_evidence(stale["evidence_id"], "QA reviewer", 2)


def test_reviewed_evidence_retracts_in_reverse_version_order():
    workflow = default_workflow()
    label = proposal(
        workflow,
        "ACT-LABEL-C100",
        workflow.example_fact("ACT-LABEL-C100"),
        "ordered-label-qa-v2",
    )
    workflow.accept_evidence(label["evidence_id"], "QA reviewer", 1)
    manifest = proposal(
        workflow,
        "ACT-MANIFEST-S200",
        workflow.example_fact("ACT-MANIFEST-S200"),
        "ordered-manifest-qa-v2",
    )
    workflow.accept_evidence(manifest["evidence_id"], "QA reviewer", 2)

    with pytest.raises(ConflictError, match="later reviewed evidence"):
        workflow.retract_evidence(
            label["evidence_id"], "QA reviewer", 3, "Older source withdrawn"
        )
