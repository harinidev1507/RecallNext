"""Deterministic, versioned RecallNext workflow for the shared fixture."""

from __future__ import annotations

import csv
import json
import threading
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from planner.allocation_bounds import classify_shipments
from planner.evidence_planner import rank_actions
from planner.scenario_generator import generate_feasible_scenarios


class WorkflowError(ValueError):
    pass


class ConflictError(WorkflowError):
    pass


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _int_rows(
    rows: list[dict[str, str]], fields: tuple[str, ...]
) -> list[dict[str, Any]]:
    return [{**row, **{field: int(row[field]) for field in fields}} for row in rows]


def _scenario_key(scenario: list[dict[str, Any]]) -> str:
    return json.dumps(scenario, sort_keys=True, separators=(",", ":"))


class RecallWorkflow:
    """In-process workflow using real planner code and labelled synthetic inputs."""

    model_version = "bounded-enumerator-v1"

    def __init__(self, data_directory: Path):
        self.data_directory = data_directory
        self.data_source = "SYNTHETIC_FIXTURE"
        self.data_source_detail = (
            "Committed CSV fixture; Exasol is not active for this process."
        )
        self._lock = threading.RLock()
        self._evidence: dict[str, dict[str, Any]] = {}
        self._load()

    def _unique_rows(
        self, rows: list[dict[str, Any]], identifier: str, label: str
    ) -> list[dict[str, Any]]:
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            value = str(row.get(identifier, "")).strip()
            if value in seen:
                self.data_quality_issues.append(f"DUPLICATE_{label}:{value}")
                continue
            seen.add(value)
            unique.append(row)
        return unique

    def _coverage_is_complete(self) -> bool:
        required = {
            row["source_system"]
            for row in _read_csv(self.data_directory / "required_source_system.csv")
        }
        coverage_rows = _read_csv(self.data_directory / "source_coverage.csv")
        coverage: dict[str, dict[str, str]] = {}
        for row in coverage_rows:
            source = row["source_system"]
            if source in coverage:
                self.data_quality_issues.append(f"DUPLICATE_SOURCE_COVERAGE:{source}")
                continue
            coverage[source] = row
        if not required or not required.issubset(coverage):
            self.data_quality_issues.append("MISSING_REQUIRED_SOURCE_COVERAGE")
            return False
        try:
            complete = all(
                row["is_complete"].strip().lower() == "true"
                and int(row["received_records"]) >= int(row["expected_records"])
                for row in coverage.values()
            )
        except (KeyError, TypeError, ValueError):
            complete = False
        if not complete:
            self.data_quality_issues.append("INCOMPLETE_SOURCE_COVERAGE")
        return complete

    def _load(self) -> None:
        self.data_quality_issues: list[str] = []
        incident = _read_csv(self.data_directory / "incident.csv")[0]
        self.incident_id = incident["incident_id"]
        self.snapshot_version = int(incident["snapshot_version"])
        self.lots = self._unique_rows(
            _int_rows(_read_csv(self.data_directory / "lot.csv"), ("quantity_cases",)),
            "lot_id",
            "LOT_ID",
        )
        self.shipments = self._unique_rows(
            _int_rows(
                _read_csv(self.data_directory / "shipment.csv"), ("quantity_cases",)
            ),
            "shipment_id",
            "SHIPMENT_ID",
        )
        self.containers = self._unique_rows(
            _int_rows(
                _read_csv(self.data_directory / "container.csv"), ("quantity_cases",)
            ),
            "container_id",
            "CONTAINER_ID",
        )
        self.shipment_containers = _int_rows(
            _read_csv(self.data_directory / "shipment_container.csv"),
            ("pick_quantity_cases",),
        )
        self.actions = self._unique_rows(
            _int_rows(
                _read_csv(self.data_directory / "evidence_action.csv"),
                ("estimated_minutes",),
            ),
            "action_id",
            "ACTION_ID",
        )
        self.action_shipments = _read_csv(self.data_directory / "action_shipment.csv")
        self.recalled_lot_ids = [
            row["lot_id"]
            for row in _read_csv(self.data_directory / "incident_recalled_lot.csv")
        ]
        self._container_by_id = {row["container_id"]: row for row in self.containers}
        self._shipments_by_id = {row["shipment_id"]: row for row in self.shipments}
        self._actions_by_id = {row["action_id"]: row for row in self.actions}
        self.candidate_edges = self._build_candidate_edges()
        candidate_universe_complete = (
            self._coverage_is_complete() and not self.data_quality_issues
        )
        generated = generate_feasible_scenarios(
            self.candidate_edges,
            self.shipments,
            self.lots,
            candidate_universe_complete=candidate_universe_complete,
            closed_inventory=True,
        )
        assumptions = {
            "candidate_universe_complete": generated["candidate_universe_complete"],
            "solver_status": generated["solver_status"],
            "inventory_balance_mode": "CLOSED",
            "synthetic_data": True,
            "data_quality_issues": list(self.data_quality_issues),
        }
        decisions = classify_shipments(
            self.lots,
            self.shipments,
            generated["candidate_allocations"],
            self.recalled_lot_ids,
            assumptions,
        )
        self.current_version = 1
        self._base_scenarios = generated["candidate_allocations"]
        self._versions: dict[int, dict[str, Any]] = {
            1: {
                "version": 1,
                "scenarios": generated["candidate_allocations"],
                "decisions": decisions,
                "solver_status": generated["solver_status"],
                "assumptions": assumptions,
                "evidence_id": None,
                "evidence_references": [],
                "diagnostics": generated.get("diagnostics", {}),
            }
        }

    def _build_candidate_edges(self) -> list[dict[str, Any]]:
        lots_by_id = {lot["lot_id"]: lot for lot in self.lots}
        edges: list[dict[str, Any]] = []
        for mapping in self.shipment_containers:
            container = self._container_by_id[mapping["container_id"]]
            pick_quantity = mapping["pick_quantity_cases"]
            if (
                container["lot_id"]
                and container["homogeneity_verified"].lower() == "true"
            ):
                candidates = [lots_by_id[container["lot_id"]]]
            else:
                candidates = [
                    lot
                    for lot in self.lots
                    if lot["product_id"] == container["product_id"]
                ]
            for lot in candidates:
                known = (
                    container["lot_id"] == lot["lot_id"]
                    and container["homogeneity_verified"].lower() == "true"
                )
                edges.append(
                    {
                        "shipment_id": mapping["shipment_id"],
                        "container_id": mapping["container_id"],
                        "lot_id": lot["lot_id"],
                        "group_quantity_cases": pick_quantity,
                        "min_quantity_cases": pick_quantity if known else 0,
                        "max_quantity_cases": min(pick_quantity, lot["quantity_cases"]),
                    }
                )
        return edges

    @staticmethod
    def _fact_quantity_map(value: object, field: str) -> dict[str, int]:
        if not isinstance(value, dict) or not value:
            raise WorkflowError(f"{field} must be a non-empty object")
        result: dict[str, int] = {}
        for lot_id, quantity in value.items():
            if not isinstance(lot_id, str) or not lot_id.strip():
                raise WorkflowError(f"{field} lot IDs must be non-empty strings")
            if (
                not isinstance(quantity, int)
                or isinstance(quantity, bool)
                or quantity <= 0
            ):
                raise WorkflowError(f"{field} quantities must be positive integers")
            result[lot_id] = quantity
        return result

    def _validate_fact(self, action_id: str, fact: object) -> None:
        action = self._actions_by_id.get(action_id)
        if action is None:
            raise WorkflowError("unknown action_id")
        if not isinstance(fact, dict):
            raise WorkflowError("proposed_fact must be an object")

        expected_type = {
            "DISPATCH_MANIFEST_LOOKUP": "shipment_allocation",
            "LABEL_LOOKUP": "homogeneous_container",
            "PICK_LOG_LOOKUP": "container_allocation",
            "PHYSICAL_SCAN": "observed_case",
        }.get(action["action_type"])
        if fact.get("fact_type") != expected_type:
            raise WorkflowError(
                f"{action['action_type']} requires fact_type {expected_type!r}"
            )

        known_lots = {lot["lot_id"] for lot in self.lots}
        target = action["target_id"]
        if expected_type == "shipment_allocation":
            if fact.get("shipment_id") != target or target not in self._shipments_by_id:
                raise WorkflowError("shipment fact target does not match the action")
            allocations = self._fact_quantity_map(
                fact.get("allocations"), "allocations"
            )
            if not set(allocations).issubset(known_lots):
                raise WorkflowError("allocations contain an unknown lot_id")
            if (
                sum(allocations.values())
                != self._shipments_by_id[target]["quantity_cases"]
            ):
                raise WorkflowError("allocations must equal the shipment quantity")
            return

        if expected_type == "homogeneous_container":
            if (
                fact.get("container_id") != target
                or target not in self._container_by_id
            ):
                raise WorkflowError("container fact target does not match the action")
            if fact.get("lot_id") not in known_lots:
                raise WorkflowError("container fact contains an unknown lot_id")
            if fact.get("homogeneity_verified") is not True:
                raise WorkflowError(
                    "homogeneous_container requires verified homogeneity"
                )
            return

        if expected_type == "container_allocation":
            if (
                fact.get("container_id") != target
                or target not in self._container_by_id
            ):
                raise WorkflowError("container fact target does not match the action")
            raw_shipments = fact.get("shipment_allocations")
            if not isinstance(raw_shipments, dict):
                raise WorkflowError("shipment_allocations must be an object")
            expected_picks = {
                row["shipment_id"]: row["pick_quantity_cases"]
                for row in self.shipment_containers
                if row["container_id"] == target
            }
            if set(raw_shipments) != set(expected_picks):
                raise WorkflowError(
                    "shipment_allocations must cover every shipment for the container"
                )
            for shipment_id, raw_allocations in raw_shipments.items():
                allocations = self._fact_quantity_map(
                    raw_allocations, f"shipment_allocations.{shipment_id}"
                )
                if not set(allocations).issubset(known_lots):
                    raise WorkflowError(
                        "shipment_allocations contain an unknown lot_id"
                    )
                if sum(allocations.values()) != expected_picks[shipment_id]:
                    raise WorkflowError(
                        "shipment allocation must equal its container pick quantity"
                    )
            return

        if fact.get("shipment_id") != target or target not in self._shipments_by_id:
            raise WorkflowError("observed case target does not match the action")
        if fact.get("lot_id") not in known_lots:
            raise WorkflowError("observed case contains an unknown lot_id")
        if fact.get("scope") != "SINGLE_CASE_ONLY":
            raise WorkflowError("observed cases must use SINGLE_CASE_ONLY scope")
        if not self._apply_fact(self._base_scenarios, fact):
            raise WorkflowError(
                "observed case lot is incompatible with the target shipment"
            )

    def _version(self, version: int | None = None) -> dict[str, Any]:
        selected = self.current_version if version is None else version
        if selected not in self._versions:
            raise WorkflowError(f"incident version {selected} does not exist")
        return self._versions[selected]

    def incident(self) -> dict[str, Any]:
        state = self._version()
        status_counts: dict[str, int] = defaultdict(int)
        for decision in state["decisions"]:
            status_counts[decision["status"]] += 1
        return {
            "incident_id": self.incident_id,
            "recalled_lots": self.recalled_lot_ids,
            "snapshot_version": self.snapshot_version,
            "current_version": self.current_version,
            "model_version": self.model_version,
            "data_source": self.data_source,
            "data_source_detail": self.data_source_detail,
            "summary": {
                "shipment_count": len(self.shipments),
                "held_cases": sum(
                    item["held_cases"]
                    for item in state["decisions"]
                    if item["status"] != "EXCLUDED_UNDER_ASSUMPTIONS"
                ),
                "status_counts": dict(status_counts),
                "feasible_scenarios": len(state["scenarios"]),
                "solver_status": state["solver_status"],
                "data_quality_issues": list(self.data_quality_issues),
            },
            "latest_diff": self.diff(self.current_version - 1, self.current_version)
            if self.current_version > 1
            else [],
        }

    def decisions(self, version: int | None = None) -> dict[str, Any]:
        state = self._version(version)
        return {
            "incident_id": self.incident_id,
            "version": state["version"],
            "model_version": self.model_version,
            "solver_status": state["solver_status"],
            "evidence_references": list(state["evidence_references"]),
            "decisions": state["decisions"],
        }

    def _apply_fact(
        self, scenarios: list[list[dict[str, Any]]], fact: dict[str, Any]
    ) -> list[list[dict[str, Any]]]:
        fact_type = fact.get("fact_type")
        if fact_type == "shipment_allocation":
            shipment_id = str(fact.get("shipment_id", ""))
            allocations = {
                str(k): int(v) for k, v in dict(fact.get("allocations", {})).items()
            }
            return [
                scenario
                for scenario in scenarios
                if self._allocation_map(
                    [row for row in scenario if row["shipment_id"] == shipment_id]
                )
                == allocations
            ]
        if fact_type == "container_allocation":
            container_id = str(fact.get("container_id", ""))
            allocations = {
                str(shipment): {str(lot): int(qty) for lot, qty in lots.items()}
                for shipment, lots in dict(fact.get("shipment_allocations", {})).items()
            }
            return [
                scenario
                for scenario in scenarios
                if {
                    shipment_id: self._allocation_map(
                        [
                            row
                            for row in scenario
                            if row["container_id"] == container_id
                            and row["shipment_id"] == shipment_id
                        ]
                    )
                    for shipment_id in allocations
                }
                == allocations
            ]
        if fact_type == "homogeneous_container":
            container_id = str(fact.get("container_id", ""))
            lot_id = str(fact.get("lot_id", ""))
            return [
                scenario
                for scenario in scenarios
                if all(
                    row["lot_id"] == lot_id
                    for row in scenario
                    if row["container_id"] == container_id
                )
            ]
        if fact_type == "observed_case":
            shipment_id = str(fact.get("shipment_id", ""))
            lot_id = str(fact.get("lot_id", ""))
            return [
                scenario
                for scenario in scenarios
                if any(
                    row["shipment_id"] == shipment_id
                    and row["lot_id"] == lot_id
                    and row["quantity_cases"] > 0
                    for row in scenario
                )
            ]
        raise WorkflowError(f"unsupported fact_type {fact_type!r}")

    @staticmethod
    def _allocation_map(rows: list[dict[str, Any]]) -> dict[str, int]:
        allocations: dict[str, int] = defaultdict(int)
        for row in rows:
            allocations[row["lot_id"]] += row["quantity_cases"]
        return dict(allocations)

    def _possible_facts(
        self, action: dict[str, Any], scenarios: list[list[dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        action_type = action["action_type"]
        target = action["target_id"]
        facts: dict[str, dict[str, Any]] = {}
        if action_type == "DISPATCH_MANIFEST_LOOKUP":
            for scenario in scenarios:
                fact = {
                    "fact_type": "shipment_allocation",
                    "shipment_id": target,
                    "allocations": self._allocation_map(
                        [row for row in scenario if row["shipment_id"] == target]
                    ),
                }
                facts[json.dumps(fact, sort_keys=True)] = fact
        elif action_type == "LABEL_LOOKUP":
            for lot in self.lots:
                fact = {
                    "fact_type": "homogeneous_container",
                    "container_id": target,
                    "lot_id": lot["lot_id"],
                    "homogeneity_verified": True,
                }
                if self._apply_fact(scenarios, fact):
                    facts[json.dumps(fact, sort_keys=True)] = fact
        elif action_type == "PICK_LOG_LOOKUP":
            shipment_ids = sorted(
                {
                    row["shipment_id"]
                    for row in self.shipment_containers
                    if row["container_id"] == target
                }
            )
            for scenario in scenarios:
                fact = {
                    "fact_type": "container_allocation",
                    "container_id": target,
                    "shipment_allocations": {
                        shipment_id: self._allocation_map(
                            [
                                row
                                for row in scenario
                                if row["container_id"] == target
                                and row["shipment_id"] == shipment_id
                            ]
                        )
                        for shipment_id in shipment_ids
                    },
                }
                facts[json.dumps(fact, sort_keys=True)] = fact
        elif action_type == "PHYSICAL_SCAN":
            for lot in self.lots:
                fact = {
                    "fact_type": "observed_case",
                    "shipment_id": target,
                    "lot_id": lot["lot_id"],
                    "scope": "SINGLE_CASE_ONLY",
                }
                if self._apply_fact(scenarios, fact):
                    facts[json.dumps(fact, sort_keys=True)] = fact
        return list(facts.values())

    def evidence_actions(self) -> dict[str, Any]:
        state = self._version()
        outcomes: dict[str, list[dict[str, Any]]] = {}
        for action in self.actions:
            action_outcomes: list[dict[str, Any]] = []
            seen_decisions: set[str] = set()
            for fact in self._possible_facts(action, state["scenarios"]):
                filtered = self._apply_fact(state["scenarios"], fact)
                if not filtered:
                    continue
                decisions = classify_shipments(
                    self.lots,
                    self.shipments,
                    filtered,
                    self.recalled_lot_ids,
                    {**state["assumptions"], "solver_status": "SUCCESS"},
                )
                key = json.dumps(decisions, sort_keys=True)
                if key not in seen_decisions:
                    seen_decisions.add(key)
                    action_outcomes.append({"outcome": "VALID", "decisions": decisions})
            action_outcomes.append(
                {"outcome": "UNAVAILABLE", "decisions": state["decisions"]}
            )
            outcomes[action["action_id"]] = action_outcomes
        ranked = rank_actions(state["decisions"], self.actions, outcomes)
        survivor_ids = {item["action_id"] for item in ranked}
        for action in self.actions:
            if action["action_id"] in survivor_ids:
                continue
            dominated = rank_actions(
                state["decisions"],
                [action],
                {action["action_id"]: outcomes[action["action_id"]]},
            )[0]
            dominated["dominated"] = True
            dominated["ranking_reason"] = (
                "Shown for transparency; another available action has no greater effort "
                "and no smaller reported benefit bounds."
            )
            ranked.append(dominated)
        affected = defaultdict(list)
        for row in self.action_shipments:
            affected[row["action_id"]].append(row["shipment_id"])
        for item in ranked:
            item.setdefault("dominated", False)
            item["affected_shipments"] = sorted(affected[item["action_id"]])
            item["possible_outcomes"] = sorted(
                {outcome["outcome"] for outcome in item["outcomes"]}
            )
        return {
            "incident_id": self.incident_id,
            "version": self.current_version,
            "actions": ranked,
        }

    def submit_evidence(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._validate_fact(payload["action_id"], payload["proposed_fact"])
            duplicate = next(
                (
                    item
                    for item in self._evidence.values()
                    if item["content_hash"] == payload["content_hash"]
                    and item["action_id"] == payload["action_id"]
                    and item["incident_version"] == self.current_version
                    and item["status"] == "PENDING_REVIEW"
                ),
                None,
            )
            if duplicate:
                return {**duplicate, "duplicate": True}
            evidence_id = f"EVD-{uuid.uuid4().hex[:12].upper()}"
            record = {
                "evidence_id": evidence_id,
                "incident_id": self.incident_id,
                "incident_version": self.current_version,
                "status": "PENDING_REVIEW",
                "verified_by": None,
                "duplicate": False,
                **payload,
            }
            self._evidence[evidence_id] = record
            return record.copy()

    def has_action(self, action_id: str) -> bool:
        return action_id in self._actions_by_id

    def reject_evidence(
        self, evidence_id: str, verified_by: str, expected_version: int, reason: str
    ) -> dict[str, Any]:
        with self._lock:
            if expected_version != self.current_version:
                raise ConflictError(
                    f"stale incident version: expected {expected_version}, current {self.current_version}"
                )
            evidence = self._evidence.get(evidence_id)
            if evidence is None:
                raise WorkflowError("evidence does not exist")
            if evidence["status"] != "PENDING_REVIEW":
                raise ConflictError(f"evidence is already {evidence['status']}")
            if evidence["incident_version"] != self.current_version:
                raise ConflictError(
                    "evidence was proposed for an earlier incident version"
                )
            self._validate_fact(evidence["action_id"], evidence["proposed_fact"])
            evidence.update(
                {
                    "status": "REJECTED",
                    "verified_by": verified_by,
                    "rejection_reason": reason,
                }
            )
            return {
                "evidence": evidence.copy(),
                "incident_id": self.incident_id,
                "current_version": self.current_version,
                "decision_diff": [],
            }

    def accept_evidence(
        self, evidence_id: str, verified_by: str, expected_version: int
    ) -> dict[str, Any]:
        with self._lock:
            if expected_version != self.current_version:
                raise ConflictError(
                    f"stale incident version: expected {expected_version}, current {self.current_version}"
                )
            evidence = self._evidence.get(evidence_id)
            if evidence is None:
                raise WorkflowError("evidence does not exist")
            if evidence["status"] != "PENDING_REVIEW":
                raise ConflictError(f"evidence is already {evidence['status']}")
            if evidence["incident_version"] != self.current_version:
                raise ConflictError(
                    "evidence was proposed for an earlier incident version"
                )
            self._validate_fact(evidence["action_id"], evidence["proposed_fact"])
            before = self._version()
            filtered = self._apply_fact(before["scenarios"], evidence["proposed_fact"])
            next_version = self.current_version + 1
            if filtered:
                solver_status = "SUCCESS"
                assumptions = {**before["assumptions"], "solver_status": solver_status}
                decisions = classify_shipments(
                    self.lots,
                    self.shipments,
                    filtered,
                    self.recalled_lot_ids,
                    assumptions,
                )
                evidence_status = "ACCEPTED"
            else:
                solver_status = "CONFLICT"
                assumptions = {**before["assumptions"], "solver_status": solver_status}
                decisions = classify_shipments(
                    self.lots, self.shipments, [], self.recalled_lot_ids, assumptions
                )
                evidence_status = "CONFLICTING"
            evidence.update(
                {
                    "status": evidence_status,
                    "verified_by": verified_by,
                    "accepted_into_version": next_version,
                }
            )
            self._versions[next_version] = {
                "version": next_version,
                "scenarios": filtered,
                "decisions": decisions,
                "solver_status": solver_status,
                "assumptions": assumptions,
                "evidence_id": evidence_id,
                "evidence_references": [
                    *before["evidence_references"],
                    evidence_id,
                ],
                "diagnostics": {"remaining_feasible_scenarios": len(filtered)},
            }
            self.current_version = next_version
            return {
                "evidence": evidence.copy(),
                "incident_id": self.incident_id,
                "previous_version": next_version - 1,
                "current_version": next_version,
                "solver_status": solver_status,
                "decision_diff": self.diff(next_version - 1, next_version),
            }

    def retract_evidence(
        self, evidence_id: str, verified_by: str, expected_version: int, reason: str
    ) -> dict[str, Any]:
        with self._lock:
            if expected_version != self.current_version:
                raise ConflictError(
                    f"stale incident version: expected {expected_version}, current {self.current_version}"
                )
            evidence = self._evidence.get(evidence_id)
            if evidence is None:
                raise WorkflowError("evidence does not exist")
            if evidence["status"] not in {"ACCEPTED", "CONFLICTING"}:
                raise ConflictError(
                    f"only accepted or conflicting evidence can be retracted; current status is {evidence['status']}"
                )
            reviewed = [
                item
                for item in self._evidence.values()
                if item["status"] in {"ACCEPTED", "CONFLICTING"}
            ]
            latest = max(reviewed, key=lambda item: item["accepted_into_version"])
            if latest["evidence_id"] != evidence_id:
                raise ConflictError(
                    "later reviewed evidence must be retracted first in this prototype"
                )

            previous_status = evidence["status"]
            evidence.update(
                {
                    "status": "RETRACTED",
                    "previous_status": previous_status,
                    "retracted_by": verified_by,
                    "retraction_reason": reason,
                }
            )
            active_evidence = sorted(
                (
                    item
                    for item in self._evidence.values()
                    if item["status"] in {"ACCEPTED", "CONFLICTING"}
                ),
                key=lambda item: item["accepted_into_version"],
            )
            filtered = self._base_scenarios
            active_references: list[str] = []
            for item in active_evidence:
                filtered = self._apply_fact(filtered, item["proposed_fact"])
                active_references.append(item["evidence_id"])

            initial = self._versions[1]
            if not filtered:
                solver_status = (
                    initial["solver_status"] if not self._base_scenarios else "CONFLICT"
                )
            else:
                solver_status = "SUCCESS"
            assumptions = {**initial["assumptions"], "solver_status": solver_status}
            decisions = classify_shipments(
                self.lots,
                self.shipments,
                filtered,
                self.recalled_lot_ids,
                assumptions,
            )
            next_version = self.current_version + 1
            evidence["retracted_into_version"] = next_version
            before_version = self.current_version
            self._versions[next_version] = {
                "version": next_version,
                "scenarios": filtered,
                "decisions": decisions,
                "solver_status": solver_status,
                "assumptions": assumptions,
                "evidence_id": evidence_id,
                "evidence_references": [*active_references, evidence_id],
                "diagnostics": {
                    "remaining_feasible_scenarios": len(filtered),
                    "active_evidence_references": active_references,
                },
            }
            self.current_version = next_version
            return {
                "evidence": evidence.copy(),
                "incident_id": self.incident_id,
                "previous_version": before_version,
                "current_version": next_version,
                "solver_status": solver_status,
                "decision_diff": self.diff(before_version, next_version),
            }

    def diff(self, from_version: int, to_version: int) -> list[dict[str, Any]]:
        before = {
            item["shipment_id"]: item
            for item in self._version(from_version)["decisions"]
        }
        after = {
            item["shipment_id"]: item for item in self._version(to_version)["decisions"]
        }
        evidence_id = self._version(to_version)["evidence_id"]
        return [
            {
                "shipment_id": shipment_id,
                "old_status": old["status"],
                "new_status": after[shipment_id]["status"],
                "old_bounds": [old["min_recalled_cases"], old["max_recalled_cases"]],
                "new_bounds": [
                    after[shipment_id]["min_recalled_cases"],
                    after[shipment_id]["max_recalled_cases"],
                ],
                "evidence_id": evidence_id,
                "remaining_unresolved_cases": after[shipment_id]["held_cases"]
                if after[shipment_id]["status"] in {"POSSIBLE_INCLUSION", "UNRESOLVED"}
                else 0,
            }
            for shipment_id, old in before.items()
            if old["status"] != after[shipment_id]["status"]
            or old["min_recalled_cases"] != after[shipment_id]["min_recalled_cases"]
            or old["max_recalled_cases"] != after[shipment_id]["max_recalled_cases"]
        ]

    @staticmethod
    def example_fact(action_id: str) -> dict[str, Any]:
        examples = {
            "ACT-MANIFEST-S200": {
                "fact_type": "shipment_allocation",
                "shipment_id": "S-200",
                "allocations": {"FARM-A:REC-2026-01": 5},
            },
            "ACT-LABEL-C100": {
                "fact_type": "homogeneous_container",
                "container_id": "C-100",
                "lot_id": "FARM-A:REC-2026-01",
                "homogeneity_verified": True,
            },
            "ACT-SCAN-C200": {
                "fact_type": "observed_case",
                "shipment_id": "S-300",
                "lot_id": "FARM-A:GOOD-2026-01",
                "scope": "SINGLE_CASE_ONLY",
            },
        }
        return examples.get(
            action_id,
            {
                "fact_type": "container_allocation",
                "container_id": "C-200",
                "shipment_allocations": {
                    "S-300": {"FARM-A:GOOD-2026-01": 5},
                    "S-400": {"FARM-A:REC-2026-01": 5},
                },
            },
        )


def default_workflow() -> RecallWorkflow:
    return RecallWorkflow(Path(__file__).resolve().parents[2] / "data" / "sample")
