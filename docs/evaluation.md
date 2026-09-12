# Evaluation record

This record separates three different verification scopes: Adya's completed
offline QA run, Sakthi's completed live Exasol boundary run, and the still
pending investigation-strategy evaluation. The committed operational data is
synthetic.

## Revision under test

- Harini integration base: `6450a4189fe1a67be379cd915a29a3c59361c5e8`
- Adya branch: `feat/adya-qa-docs-demo`
- Combined integration baseline: `940890269d4113bb30a370047620573cfb0faae9`
- Live Exasol revision: `fcf2a83ee6d77c0cb47b6a7f1e16fcff4fc834bc`

The raw offline output file records Adya's original base and commands. It is
historical evidence for that run, not a live Exasol measurement and not a claim
that every later integration commit ran on the same Mac.

## Offline QA machine and tools

| Item | Recorded value |
|---|---|
| Date | 12 September 2026 |
| Host | MacBook Air, Apple M5, 16 GB RAM |
| OS | macOS 26.5.1, arm64 |
| Python | 3.12.14 |
| pytest | 8.4.2 |
| PyExasol | 2.4.0 |
| Ruff | 0.16.7 |
| Node.js | 24.19.0 |
| pnpm | 11.19.0 |
| Exasol | Not run on this Mac; see the separate AWS live run below |

## Completed offline checks

Fixture version: deterministic `data.generate_fixture` output and adversarial
matrix `qa-v2`.

| Check | Result | Evidence |
|---|---|---|
| Editable Python install | PASS | `docs/evaluation-results/offline-qa.txt` |
| Python unit, workflow and API tests | PASS — 79 tests | `docs/evaluation-results/offline-qa.txt` |
| Deterministic fixture regeneration | PASS | `docs/evaluation-results/offline-qa.txt` |
| Ruff lint | PASS | `docs/evaluation-results/offline-qa.txt` |
| Ruff format check | PASS — 34 files | `docs/evaluation-results/offline-qa.txt` |
| pnpm frozen-lockfile install | PASS | `docs/evaluation-results/offline-qa.txt` |
| TypeScript and Vite production build | PASS | `docs/evaluation-results/offline-qa.txt` |
| Local visual workflow smoke | PASS | `docs/evaluation-results/offline-qa.txt` |

The test duration and Vite build time are local tool runtimes. They are not
database, solver or end-to-end investigation latency.

### Post-review integration verification

The Greptile follow-up fixes were rerun on 13 September 2026 in a clean Python
3.11 environment on Windows. This is a separate run from the historical Mac
artifact above:

| Check | Result |
|---|---|
| Python tests | PASS - 83 tests, one dependency deprecation warning |
| Ruff lint | PASS |
| Ruff format check | PASS - 34 files |
| Deterministic fixture regeneration | PASS |
| pnpm frozen-lockfile install | PASS - pnpm 11.19.0 |
| TypeScript and Vite production build | PASS |

This rerun covers the workflow, regression tests, and documentation changes in
the review-fix head. It is not a replacement for the revision-scoped live
Exasol record below.

## Correctness scope exercised

The offline suite covers:

- all four fixed decision statuses and independently checked tiny bounds;
- a 125-scenario closed synthetic inventory with four possible and two excluded
  shipments;
- exact shipment and lot conservation, invalid edges, empty scenario sets and
  configured enumeration limits, including a closed inventory with a
  zero-quantity lot;
- missing required source coverage and duplicate inventory identifiers;
- an additional complete optional source that does not invalidate coverage of
  the required source roster;
- source-qualified lot identity when two suppliers reuse one lot code;
- mixed-container single-case evidence that tightens only the observed case and
  cannot clear the remaining cases;
- incompatible accepted label and pick-log evidence producing `CONFLICT` and
  `UNRESOLVED`;
- rejected and unavailable evidence producing no narrowing;
- version conflicts, proposals made stale by another acceptance, current
  pending deduplication, resubmission after rejection or version advance, and
  malformed facts;
- accepted-evidence retraction rebuilding from active evidence and invalidating
  a prior exclusion;
- retraction of the newest conflicting record while an older active conflict
  continues to keep every decision unresolved;
- persistence rejection when status, bounds, solver status and completeness
  metadata disagree;
- fixture replacement refusal before deletion when another incident exists;
- partial action outcomes and conditional-value dominance behavior.

These checks establish behavior only for the declared finite inputs. They do
not certify food safety, prove warehouse-scale performance or validate the SQL
on an Exasol engine.

## Live Exasol verification status

The Exasol boundary was run successfully on 12 September 2026 against a real,
containerized Exasol Personal starter-kit deployment on AWS EC2. The verified
revision was `fcf2a83ee6d77c0cb47b6a7f1e16fcff4fc834bc`.

That run established:

- Exasol image `docker.io/exasol/nano:2026.2.0-nano.3-amd64`;
- successful schema and synthetic-fixture loading;
- six shipments and fourteen candidate edges;
- zero blocking data-quality issues;
- `candidate_universe_complete: true`;
- encrypted PyExasol transport with a pinned certificate fingerprint; and
- a live duplicate source-qualified lot probe that blocked narrowing and was
  removed after verification.

The credential-free commands, raw counts, single-run query timings, and
environment details are recorded in `docs/live-exasol-verification.md`.

The later combined integration baseline `9408902` changed SQL and loader files
after the recorded `fcf2a83` run. Therefore the live record proves the named
revision, while an exact-head database rerun remains required before claiming
that every integrated SQL and loader change was exercised live. This distinction
does not invalidate the completed run, and it must not be described as either
fully pending or as exact-head verification.

The web API still reads the committed CSV fixture and labels that source. It
must not be described as Exasol-backed until the database adapter is wired into
the request path.

## Pending investigation-strategy experiment

The baseline module produces deterministic action orders. It does not yet run a
sequential experiment that applies evidence, recomputes rankings and scores
each strategy.

| Strategy | Current state |
|---|---|
| Hold all plausible inventory | Ordering implemented; sequential score pending |
| Deterministic random action | Ordering implemented; multi-seed score pending |
| Cheapest evidence first | Ordering implemented; score pending |
| Highest directly involved quantity first | Ordering implemented; score pending |
| RecallNext ranking | One-step ordering implemented; sequential score pending |
| Full-information oracle | Tiny correctness utility only |

A fair run must give every policy the same incidents, visible facts, obtainable
evidence, realized outcomes and budget. Retain per-incident seeds, ties, errors
and simple-baseline wins. Report false exclusions, affected-case coverage,
unnecessary held cases, resolved cases, actions, simulated retrieval minutes,
replay agreement and actual computation time.

## Known unavailable checks

- The offline Mac QA run had no local Exasol deployment or team connection, so
  that machine did not produce database results. A separate AWS live run did,
  as recorded above, but the later combined integration head still needs its
  own database rerun.
- No live document model is connected. Example facts are labelled synthetic;
  no API credits were used.
- The frontend has a production build gate and a recorded manual visual smoke,
  but no repeatable browser automation suite.
- Retraction is available through the API and covered by tests; the current UI
  has no retraction control.
- Evidence and incident versions are stored in process memory and reset when
  the API restarts.

## Reporting rules

Do not present expected counts as live database measurements. Do not claim a
faster investigation until the paired sequential experiment supports it. Keep
retrieval-minute assumptions separate from measured runtime, and never treat
`EXCLUDED_UNDER_ASSUMPTIONS` as safe or released inventory.
