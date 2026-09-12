# RecallNext Exasol core

This branch owns the real Exasol boundary: schema creation, deterministic
fixture loading, conservative candidate-edge generation, result persistence,
and the adapter consumed by the planner. It contains no SQLite or in-memory
database fallback.

## What is implemented

- `sql/001_schema.sql`: the shared traceability tables plus the required-source
  roster, source coverage, scenario, and decision audit tables.
- `sql/002_views.sql`: blocking data-quality issues, candidate-universe
  completeness, and the flat planner-scenario contract.
- `sql/003_candidate_generation.sql`: broad candidate edges, shipment impact,
  and evidence-action impact.
- `data/sample`: a labelled synthetic fixture with three lots, three
  containers, six shipments, two missing pick references, and the same lot code
  under two different suppliers.
- `data/load_fixture.py`: a transaction-controlled PyExasol loader that refuses
  to overwrite the demo incident unless `--replace-demo` is explicit and the
  schema contains no other incident.
- `backend/services/incident_service.py`: incident, candidate, scenario,
  decision, and evidence-action data services.
- `backend/contracts.py`: strict conversion of flat Exasol scenario rows into
  Bhavyasha's nested planner input.

## Connection setup

Install the Exasol Personal launcher and deploy using the target appropriate for
your machine and cloud account. Do not create paid cloud resources merely to run
unit tests. Once a team deployment exists, use `exasol info` to obtain its
current connection details.

Create a virtual environment and install RecallNext:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Set the real values reported for the deployment. Do not commit them:

```powershell
$env:EXASOL_DSN = "<host>/<sha256-certificate-fingerprint>:<port>"
$env:EXASOL_USER = "<database-user>"
$env:EXASOL_PASSWORD = "<database-password>"
$env:EXASOL_SCHEMA = "RECALLNEXT"
$env:EXASOL_ENCRYPTION = "true"
```

The password is mandatory and no sample credential is used by the application.
Encryption defaults to enabled. Do not disable certificate verification in
code or use PyExasol's `/nocertcheck` option. For a starter-kit deployment with
a self-signed certificate, pin the SHA-256 certificate fingerprint in the DSN
as shown above. For a deployment with a publicly trusted certificate, use the
connection details supplied by that deployment.

## Verify and load

First run the offline checks:

```powershell
python -m data.generate_fixture --check
python -m pytest
ruff check backend data tests
```

Then create the schema and load the fixture into the configured real database:

```powershell
python -m data.load_fixture
```

The default load is non-destructive and fails if `INC-DEMO-001` already exists.
The explicit `--replace-demo` option is restricted to a demo-only schema. It
aborts before deletion when any other incident exists because the schema's
global shipment, container, lot and event identifiers do not record ownership.

Run the read-only database smoke check:

```powershell
python -m backend.smoke
```

For the committed fixture, a successful live check must report:

- six shipments;
- fourteen candidate edges;
- zero blocking data-quality issues;
- `candidate_universe_complete: true`.

Record the printed database and query timings only after this command runs on
the actual team deployment. Do not present offline test timings as Exasol
performance.

## Candidate semantics

`V_CANDIDATE_ALLOCATION` produces allowed edges, the exact container-pick group
quantity, and integer lower/upper lot quantities. It does not claim that each
row is a complete historical scenario.
Unknown container lots match every compatible, chronologically possible lot.
An unknown receipt or shipment time remains eligible and separately creates a
blocking data-quality issue; it is not filtered out as impossible.

A scenario is one full, internally consistent allocation satisfying shipment
totals, lot conservation, container rules, chronology, and accepted evidence.
Bhavyasha's solver owns that step. Solver outputs are written to
`ALLOCATION_SCENARIO` and `SCENARIO_ALLOCATION`; the
`V_PLANNER_SCENARIO_ALLOCATION` view exposes flat rows with `scenario_id` for
grouping.

The Python adapter returns:

```json
{
  "candidate_allocations": [
    [
      {
        "shipment_id": "S-100",
        "lot_id": "FARM-A:REC-2026-01",
        "quantity_cases": 5
      }
    ]
  ],
  "assumptions": {
    "candidate_universe_complete": true,
    "solver_status": "SUCCESS"
  }
}
```

The example shows the transport shape, not a complete fixture scenario. In the
database, `lot_id` is always the source-qualified key
`LOT_SOURCE_ID:LOT_CODE`. `FARM-A:REC-2026-01` and
`FARM-B:REC-2026-01` are different lots.

## Safety behavior

- A missing required-source roster or missing coverage for any roster entry
  blocks scope narrowing.
- Duplicate `LOT_SOURCE_ID:LOT_CODE` identities are detected by a blocking
  data-quality rule because Exasol Personal does not support a `UNIQUE` table
  constraint.
- A raw candidate edge cannot be passed off as a feasible scenario.
- Unknown timestamps broaden candidates and block narrowing.
- Negative or fractional case quantities are rejected.
- Duplicate scenario/shipment/lot rows are rejected rather than double-counted.
- A non-success solver result remains visible and leads the planner to
  `UNRESOLVED`.
- Persisting decisions accepts only the four fixed RecallNext status values.
- An exclusion is limited to this recall under recorded assumptions; it is not
  a claim that food is safe to consume.

## Current verification boundary

Offline tests validate contracts, fixture invariants, and required SQL safety
clauses. The schema, fixture loader, and smoke oracle were also run against a
real Exasol Personal deployment. See `docs/live-exasol-verification.md` for the
credential-free execution record. Any later SQL or loader change must repeat
`python -m data.load_fixture` and `python -m backend.smoke` against Exasol.
