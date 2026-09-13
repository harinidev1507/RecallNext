# RecallNext

RecallNext is an Exasol-powered prototype for food-recall investigations with incomplete lot-to-shipment records. It preserves every feasible allocation for a bounded incident, calculates recalled-case bounds, and ranks which obtainable record could resolve the most uncertainty for the effort required.

AI-assisted extraction may propose a structured fact. A human must accept it before deterministic reassessment creates a new incident version. The application never authorizes a physical stock release.

Live deployment: https://recallnext.13-201-33-157.nip.io/

Public visitors can explore the Exasol-backed incident and decisions. Evidence extraction and every state-changing action require the separately shared reviewer token.

## Project presentation

[View the RecallNext project presentation (PDF)](docs/presentation/RecallNext_Project_Presentation.pdf)

## What works

- deterministic generation of 125 feasible histories for the committed six-shipment fixture;
- four conservative decision states: `CONFIRMED_INCLUSION`, `POSSIBLE_INCLUSION`, `EXCLUDED_UNDER_ASSUMPTIONS`, and `UNRESOLVED`;
- outcome-aware evidence ranking, including unavailable outcomes and explicitly conditional benefits;
- FastAPI endpoints for incident scope, decisions, evidence actions, proposal, acceptance, rejection, retraction and decision differences;
- React investigation UI with API-discovered incidents, text-and-colour statuses, source review, retraction and version changes;
- Exasol schema, candidate-generation SQL, fixture loader, persistence services and a smoke check;
- a fail-closed `EXASOL_PERSONAL` API mode that reads the visible workflow from Exasol and persists reviewed evidence across API restarts;
- an openFDA importer that stores an official public recall record as context without claiming that public data contains private warehouse movements;
- source-coverage and duplicate-inventory gates before scenario generation;
- adversarial checks for invalid scenario coverage, conflicts, stale reviews, mixed containers, source-qualified lot identity, retraction and bounded computation.

The web application starts in `SYNTHETIC_FIXTURE` mode for offline development. Set `RECALLNEXT_DATA_SOURCE=EXASOL_PERSONAL` with valid connection settings to make the visible API load the incident, candidates and public recall context from Exasol. This mode fails at startup if the real database is unavailable; it never falls back to CSV.

The warehouse movements remain a labelled synthetic fixture because public recall feeds do not expose a company's private lot-to-shipment records. The optional public recall context is fetched from the official openFDA food enforcement API and stored in Exasol with source metadata.

## Demo flow

1. Open incident `INC-DEMO-001`. Four shipments are possible inclusions; two are excluded under assumptions because their homogeneous container is source-qualified to another supplier.
2. Compare label, pick-log, retained-case scan and dispatch-manifest actions. Failed retrieval is included, so guaranteed benefit can be zero; valid-outcome benefits are labelled conditional.
3. Select an action and review the synthetic proposed fact. Saving it does not alter decisions.
4. Enter a reviewer name and accept it. The API checks the expected incident version, filters feasible histories and creates a decision diff.
5. Submit an impossible source-qualified allocation to see a conflict create an `UNRESOLVED` version rather than a false exclusion.
6. Retract the latest reviewed evidence in the UI to rebuild the incident from the original snapshot and the remaining active evidence.

## Quick start

Prerequisites: Python 3.10+, Node.js 24+, and pnpm 11. From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
export RECALLNEXT_REQUIRE_AUTH=false
python -m uvicorn backend.app:app --reload
```

In a second terminal, from the repository root:

```bash
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend dev
```

Open <http://127.0.0.1:5173>. Run `python -m pytest -q` and
`pnpm --dir frontend build` before committing. The frontend uses pnpm only.

To use the database-backed path, load the schema and fixture, import a selected
openFDA food enforcement record, then start the API with:

```bash
export RECALLNEXT_DATA_SOURCE=EXASOL_PERSONAL
export RECALLNEXT_INCIDENT_ID="<incident-id>"
export RECALLNEXT_REQUIRE_AUTH=false
export EXASOL_DSN="<host>/<pinned-certificate-fingerprint>:<port>"
export EXASOL_USER="<database-user>"
export EXASOL_PASSWORD="<database-password>"
python -m data.import_openfda --incident-id "<incident-id>" --recall-number "<recall-number>"
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

See [docs/run-guide.md](docs/run-guide.md) for Exasol setup, smoke checks and troubleshooting. The API payloads are documented in [docs/api-contract.md](docs/api-contract.md).

For the production container build, protected write routes, live OpenAI
document extraction, and HTTPS edge options, follow
[docs/production-deployment.md](docs/production-deployment.md).

## Architecture

```text
warehouse fixture / Exasol candidate views
                 │
       completeness and quality gates
                 │
 bounded complete-scenario enumerator
                 │
 recalled quantity bounds per shipment
                 │
 evidence outcome simulation and ranking
                 │
 proposed fact -> human acceptance -> new version -> decision diff
```

Exasol is responsible for relational validation, candidate generation, aggregation and result storage. The Python component operates only on a bounded incident component. It aborts without partial output when its configured combination or scenario limit is exceeded.

## Safety boundaries

- Unknown coverage, invalid scenarios, conflicts and computation limits return `UNRESOLVED`.
- Empty feasible sets are conflicts, never proof of zero exposure.
- Lot identity combines lot source and lot code.
- A single case scan does not establish the contents of an unverified mixed container.
- Saving unreviewed evidence never changes a decision.
- Every acceptance uses an expected incident version to prevent a stale review.
- Retraction invalidates the affected result and creates a new version.
- Exasol-backed evidence state is restored after API restarts and rejected if it does not match the current snapshot fingerprint.
- “Excluded under assumptions” is specific to this synthetic recall model. It does not mean safe to consume.

The fixture is synthetic and describes fictional warehouse records. This project is a hackathon decision-support prototype, not regulatory advice, food-safety certification or a production warehouse integration.

## Project layout

- `backend/` - API, request models, Exasol services and workflow orchestration
- `planner/` - scenario enumeration, bounds, ranking, models and independent tiny oracle
- `sql/` - Exasol schema, quality views and candidate queries
- `data/` - deterministic generator, CSV fixture and Exasol loader
- `frontend/` - React/TypeScript investigation interface
- `tests/` - unit, API and adversarial workflow tests
- `docs/` - run guide, API, architecture, evaluation and safety notes

Verified evaluation results and their measurement boundaries are documented in
[docs/evaluation.md](docs/evaluation.md).

## Team

- Sakthi - Exasol core and data services
- Bhavyasha - allocation and evidence planner
- Harini - API, UI, integration and release
- Adya - QA, documentation and demo

Licensed under the MIT License. Third-party packages retain their own licenses.
