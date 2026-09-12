"""Bootstrap RecallNext and load its deterministic fixture into real Exasol."""

from __future__ import annotations

import argparse
import csv
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.config import ExasolConfig
from backend.db import bootstrap_schema, connect_exasol
from data.generate_fixture import TABLE_FIELDS, check_fixture


@dataclass(frozen=True)
class TableSpec:
    csv_name: str
    table_name: str

    @property
    def fields(self) -> tuple[str, ...]:
        return TABLE_FIELDS[self.csv_name]


TABLE_SPECS = (
    TableSpec("incident", "INCIDENT"),
    TableSpec("lot", "LOT"),
    TableSpec("incident_recalled_lot", "INCIDENT_RECALLED_LOT"),
    TableSpec("container", "CONTAINER"),
    TableSpec("shipment", "SHIPMENT"),
    TableSpec("shipment_container", "SHIPMENT_CONTAINER"),
    TableSpec("event", "EVENT"),
    TableSpec("required_source_system", "REQUIRED_SOURCE_SYSTEM"),
    TableSpec("source_coverage", "SOURCE_COVERAGE"),
    TableSpec("evidence_action", "EVIDENCE_ACTION"),
    TableSpec("action_shipment", "ACTION_SHIPMENT"),
)

INCIDENT_SCOPED_TABLES = (
    "INCIDENT",
    "INCIDENT_RECALLED_LOT",
    "SOURCE_COVERAGE",
    "REQUIRED_SOURCE_SYSTEM",
    "EVIDENCE_ACTION",
    "SHIPMENT_DECISION",
    "ALLOCATION_SCENARIO",
    "SCENARIO_ALLOCATION",
)

INTEGER_FIELDS = {
    "incident_version",
    "snapshot_version",
    "quantity_cases",
    "pick_quantity_cases",
    "expected_records",
    "received_records",
    "estimated_minutes",
}
BOOLEAN_FIELDS = {"recalled", "homogeneity_verified", "is_complete"}
TIMESTAMP_FIELDS = {
    "window_start",
    "window_end",
    "created_at",
    "received_at",
    "ship_time",
    "event_time",
    "recorded_at",
}
NULLABLE_FIELDS = {
    "window_start",
    "window_end",
    "received_at",
    "location_id",
    "source_event_id",
    "lot_id",
    "ship_time",
    "pick_record_id",
    "event_time",
    "source_document_id",
    "issue_detail",
}


def _convert(field: str, raw: str) -> object:
    value = raw.strip()
    if not value:
        if field in NULLABLE_FIELDS:
            return None
        raise ValueError(f"{field} is required")
    if field in INTEGER_FIELDS:
        number = int(value)
        if number < 0:
            raise ValueError(f"{field} cannot be negative")
        return number
    if field in BOOLEAN_FIELDS:
        normalized = value.lower()
        if normalized not in {"true", "false"}:
            raise ValueError(f"{field} must be true or false")
        return normalized == "true"
    if field in TIMESTAMP_FIELDS:
        return datetime.fromisoformat(value)
    return value


def _to_wire_value(value: object) -> object:
    """Convert validated values to PyExasol's JSON-serializable wire format."""

    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return value


def read_typed_rows(data_directory: Path, spec: TableSpec) -> list[tuple[object, ...]]:
    path = data_directory / f"{spec.csv_name}.csv"
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != spec.fields:
            raise ValueError(f"unexpected CSV header: {path}")
        return [
            tuple(_convert(field, row[field]) for field in spec.fields)
            for row in reader
        ]


def _incident_exists(connection: Any, incident_id: str) -> bool:
    statement = connection.execute(
        "SELECT COUNT(*) AS row_count FROM RECALLNEXT.INCIDENT "
        "WHERE INCIDENT_ID = {incident_id}",
        {"incident_id": incident_id},
    )
    row = statement.fetchone()
    return bool(row and int(row["row_count"]) > 0)


def _other_incident_exists(connection: Any, incident_id: str) -> bool:
    for table in INCIDENT_SCOPED_TABLES:
        statement = connection.execute(
            f"SELECT COUNT(*) AS row_count FROM RECALLNEXT.{table} "
            "WHERE INCIDENT_ID <> {incident_id}",
            {"incident_id": incident_id},
        )
        row = statement.fetchone()
        if row and int(row["row_count"]) > 0:
            return True
    return False


def _delete_by_value(
    connection: Any, table: str, field: str, values: Iterable[str]
) -> None:
    for value in values:
        connection.execute(
            f"DELETE FROM RECALLNEXT.{table} WHERE {field} = {{value}}",
            {"value": value},
        )


def remove_demo_fixture(
    connection: Any, data_directory: Path, incident_id: str
) -> None:
    """Delete the fixture only from a database dedicated to this demo incident."""

    if _other_incident_exists(connection, incident_id):
        raise RuntimeError(
            "--replace-demo is disabled while non-demo incidents exist; "
            "use a dedicated demo schema to avoid deleting shared global records"
        )

    actions = [
        row[0]
        for row in read_typed_rows(
            data_directory,
            next(spec for spec in TABLE_SPECS if spec.csv_name == "evidence_action"),
        )
    ]
    shipments = [
        row[0]
        for row in read_typed_rows(
            data_directory,
            next(spec for spec in TABLE_SPECS if spec.csv_name == "shipment"),
        )
    ]
    containers = [
        row[0]
        for row in read_typed_rows(
            data_directory,
            next(spec for spec in TABLE_SPECS if spec.csv_name == "container"),
        )
    ]
    lots = [
        row[0]
        for row in read_typed_rows(
            data_directory, next(spec for spec in TABLE_SPECS if spec.csv_name == "lot")
        )
    ]
    events = [
        row[0]
        for row in read_typed_rows(
            data_directory,
            next(spec for spec in TABLE_SPECS if spec.csv_name == "event"),
        )
    ]

    _delete_by_value(connection, "ACTION_SHIPMENT", "ACTION_ID", actions)
    _delete_by_value(connection, "EVIDENCE", "ACTION_ID", actions)
    connection.execute(
        "DELETE FROM RECALLNEXT.EVIDENCE_ACTION WHERE INCIDENT_ID = {incident_id}",
        {"incident_id": incident_id},
    )
    connection.execute(
        "DELETE FROM RECALLNEXT.SHIPMENT_DECISION WHERE INCIDENT_ID = {incident_id}",
        {"incident_id": incident_id},
    )
    connection.execute(
        "DELETE FROM RECALLNEXT.SCENARIO_ALLOCATION WHERE INCIDENT_ID = {incident_id}",
        {"incident_id": incident_id},
    )
    connection.execute(
        "DELETE FROM RECALLNEXT.ALLOCATION_SCENARIO WHERE INCIDENT_ID = {incident_id}",
        {"incident_id": incident_id},
    )
    connection.execute(
        "DELETE FROM RECALLNEXT.SOURCE_COVERAGE WHERE INCIDENT_ID = {incident_id}",
        {"incident_id": incident_id},
    )
    connection.execute(
        "DELETE FROM RECALLNEXT.REQUIRED_SOURCE_SYSTEM "
        "WHERE INCIDENT_ID = {incident_id}",
        {"incident_id": incident_id},
    )
    connection.execute(
        "DELETE FROM RECALLNEXT.INCIDENT_RECALLED_LOT "
        "WHERE INCIDENT_ID = {incident_id}",
        {"incident_id": incident_id},
    )
    _delete_by_value(connection, "SHIPMENT_CONTAINER", "SHIPMENT_ID", shipments)
    _delete_by_value(connection, "SHIPMENT", "SHIPMENT_ID", shipments)
    _delete_by_value(connection, "CONTAINER", "CONTAINER_ID", containers)
    _delete_by_value(connection, "LOT", "LOT_ID", lots)
    _delete_by_value(connection, "EVENT", "EVENT_ID", events)
    connection.execute(
        "DELETE FROM RECALLNEXT.INCIDENT WHERE INCIDENT_ID = {incident_id}",
        {"incident_id": incident_id},
    )


def load_fixture(
    connection: Any, data_directory: Path, *, replace_demo: bool = False
) -> dict[str, int]:
    check_fixture(data_directory)
    incident_id = "INC-DEMO-001"
    if _incident_exists(connection, incident_id):
        if not replace_demo:
            raise RuntimeError(
                "INC-DEMO-001 already exists; rerun with --replace-demo only "
                "to replace this synthetic fixture"
            )
        remove_demo_fixture(connection, data_directory, incident_id)

    counts: dict[str, int] = {}
    for spec in TABLE_SPECS:
        rows = read_typed_rows(data_directory, spec)
        wire_rows = [tuple(_to_wire_value(value) for value in row) for row in rows]
        columns = ", ".join(field.upper() for field in spec.fields)
        placeholders = ", ".join("?" for _ in spec.fields)
        statement = connection.create_prepared_statement(
            f"INSERT INTO RECALLNEXT.{spec.table_name} ({columns}) "
            f"VALUES ({placeholders})"
        )
        if wire_rows:
            statement.execute_prepared(wire_rows)
        counts[spec.table_name] = len(rows)
    return counts


def _parser() -> argparse.ArgumentParser:
    repository_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=repository_root / "data" / "sample",
    )
    parser.add_argument(
        "--sql-dir",
        type=Path,
        default=repository_root / "sql",
    )
    parser.add_argument(
        "--replace-demo",
        action="store_true",
        help="replace only the committed synthetic fixture identifiers",
    )
    return parser


def main(arguments: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    config = ExasolConfig.from_environment()
    connection = connect_exasol(config, autocommit=False, open_schema=False)
    try:
        bootstrap_schema(connection, args.sql_dir)
        counts = load_fixture(connection, args.data_dir, replace_demo=args.replace_demo)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()

    loaded = ", ".join(f"{table}={count}" for table, count in counts.items())
    print(f"Loaded synthetic fixture into {config.schema}: {loaded}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
