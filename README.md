# Sentinel Warehouse

**A financial integrity and security research platform for analyzing complex financial
systems.**

![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-17-4169E1?logo=postgresql&logoColor=white)
[![Tests](https://img.shields.io/badge/tests-88%20passing-brightgreen)](#quality)
[![License](https://img.shields.io/badge/license-not%20yet%20specified-lightgrey)](#license)

Sentinel Warehouse combines production-inspired data engineering with reproducible financial
security research. It ingests source data, reconstructs financial state from canonical events,
tests that state against explicit invariants, and preserves failures as auditable incidents and
versioned research data.

> Release: **v0.1.0 — Financial Integrity Foundation**

## Problem statement

Modern financial systems are difficult to trust.

The challenge is not only storing transactions. It is proving that the resulting system state
is:

- **Correct** — balances and protocol state obey defined financial rules.
- **Reproducible** — the same ordered events reconstruct the same state.
- **Traceable** — every derived record retains source and batch lineage.
- **Explainable** — failures retain structured evidence for investigation.

Sentinel treats a financial system as an **event-driven state machine**. Source records are
preserved immutably, normalized into canonical financial events, replayed into state, and
checked against global and protocol-specific invariants.

## How it works

```text
Financial sources
      |
      v
Immutable raw evidence
      |
      v
Canonical financial events
      |
      v
State reconstruction
      |
      v
Invariant validation
      |
      +---- accepted --> reconstructed state + explicit evidence status
      |
      +---- invalid --> incident + evidence --> research dataset
```

See the [system architecture diagram](docs/diagrams/architecture.md) and
[architecture reference](docs/architecture.md) for the full design.

## Key features

### Data Engineering

- Immutable, source-aligned raw ingestion
- Batch lineage and audited lifecycle transitions
- Durable source checkpoints
- Retry-safe execution
- Idempotent logical batches and replay protection
- Configurable data-quality policies

### Financial Modeling

- Source-independent canonical financial events
- Deterministic state and balance reconstruction
- Traditional account, asset, transaction, and balance entities
- Value-conservation and balance-snapshot validation

### Security

- Global and protocol-specific invariant engine
- Checker-owned validation context and supply authority registry
- Explicit `PASSED`, `FAILED`, and `INSUFFICIENT_EVIDENCE` outcomes
- Automatic incident detection
- Structured evidence preservation
- Isolated `LIVE`, `REPLAY`, and `FIXTURE` findings
- Retry-aware incident resolution
- Reproducible historical incident replay

### Blockchain

- Bounded Ethereum JSON-RPC ingestion
- Finality-aware block ranges and durable chain checkpoints
- Canonical-chain tracking and reorganization recovery
- Chain-native `(block, transaction, log)` event ordering
- Explicit supported, partially supported, and unsupported analysis status
- Protocol plugin interface: `detect()`, `normalize()`, and `invariants()`
- ERC-20 transfers and Uniswap V2 `Mint`, `Burn`, `Swap`, and `Sync`

### Research Dataset

- Versioned, provenance-complete incident cases
- Normalized attack taxonomy and labels
- Deterministic numeric, categorical, and sequence-aware features
- Pre-export corpus validation
- Zstandard-compressed, ML-ready Parquet export
- Exact `Decimal128(38,18)` financial values
- Dataset, extractor, schema, timestamp, and Git revision metadata
- Separate local Parquet research lake with deterministic large-dataset generators
- Direct DuckDB inspection and fixed workstation benchmark suites

### Investigation Reports

- Self-contained case and incident HTML reports
- Deterministic event timelines with chain-native ordering
- Before/after balance and delta presentation
- Inline SVG balance and relationship visualizations
- Invariant reasoning and structured evidence in one offline artifact

## Quick start

Prerequisites:

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- Docker with Compose

```bash
cp .env.example .env
uv sync
docker compose up -d postgres
uv run sentinel db migrate
uv run sentinel db status
```

Run a deterministic financial ingestion:

```bash
uv run sentinel ingest fixture data/fixtures/synthetic_financial.json
```

Ingest ERC-20 transfer fixtures:

```bash
uv run sentinel ingest ethereum data/fixtures/ethereum_valid_transfers.json
```

Import, replay, and extract features from a research case:

```bash
uv run sentinel case import data/incidents/euler_style_accounting_failure.json
uv run sentinel case replay 2ea83b7e-ae7d-4cd5-83db-096f828b8a01
uv run sentinel case features 2ea83b7e-ae7d-4cd5-83db-096f828b8a01
```

Build the validated research dataset:

```bash
uv run sentinel case import data/incidents/reconciled_transfer_control.json
uv run sentinel dataset validate
uv run sentinel dataset export
```

The default artifact is written to `data/exports/security_incidents.parquet`.

## Local research environment

The application database and large analytical datasets are intentionally separate:
PostgreSQL owns pipeline and investigation state, while `data/research/` holds local Parquet
experiments. Enter the pinned Nix development shell and install the optional research group:

```bash
nix develop
uv sync --group research
```

Generate a small deterministic dataset, inspect it directly with DuckDB, and benchmark the
fixed analytical query suite:

```bash
uv run sentinel research generate
uv run --group research sentinel research inspect \
  data/research/generated/synthetic-seed-7-rows-10000
scripts/benchmark-duckdb.sh \
  data/research/generated/synthetic-seed-7-rows-10000
```

Generation is explicit, defaults to 10,000 events, and never creates a million-row dataset
during setup or tests. See the [NixOS research workstation guide](docs/nixos.md) for storage,
PostgreSQL tuning, DuckDB SQL, capacity planning, and isolated benchmark commands.

## Investigation Reports

Sentinel converts ordered events, reconstructed state, invariant outcomes, and evidence into a
reproducible report that a security engineer or reviewer can open without a database or network
connection. Reports use deterministic templates and inline SVG; they contain no JavaScript,
CDN assets, frontend framework, or external runtime dependency.

Generate a case report by UUID, exact name, or an unambiguous name prefix:

```bash
uv run sentinel case report euler-style \
  --output reports/euler.html
```

Generate a report for an existing incident:

```bash
uv run sentinel incident report <incident-id> \
  --output reports/incident.html
```

Each report contains an executive summary, chronological timeline, before/after state and
balance deltas, an event relationship graph, invariant outcomes, evidence payloads, hashes
when available, origin, and case/incident identity.

## Investigation workflow

Invariant failures are not discarded as pipeline errors. Sentinel stores the failed invariant,
creates or updates an incident, and attaches structured evidence:

```bash
uv run sentinel ingest fixture data/fixtures/event_create_money.json
uv run sentinel incident list --origin FIXTURE
uv run sentinel incident list --origin LIVE
uv run sentinel incident list --origin REPLAY
uv run sentinel incident show <incident-id>
```

Representative terminal sessions:

- [Incident investigation](docs/examples/incident-investigation.txt)
- [Static investigation report](docs/examples/investigation-report.txt)
- [Dataset validation and export](docs/examples/dataset-validation.txt)
- [Ethereum ingestion](docs/examples/ethereum-ingestion.txt)

## Ethereum RPC ingestion

Live ingestion is deliberately bounded to explicit historical ranges. Configure a private
JSON-RPC endpoint without committing credentials:

```bash
export SENTINEL_ETHEREUM_RPC_URL='https://your-mainnet-provider.example'
uv run sentinel ingest ethereum-rpc \
  --from-block 19000000 \
  --to-block 19000010 \
  --contract 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48
```

The adapter respects the configured confirmation depth, persists observed and canonical chain
identity, advances checkpoints transactionally, and stops on an unsafe deep reorganization.
There is no continuous indexer or network dependency in the test suite.

## Documentation

| Document | Purpose |
| --- | --- |
| [Concepts](docs/concepts.md) | Financial events, invariants, incidents, and source independence |
| [Architecture](docs/architecture.md) | Detailed storage, pipeline, retry, chain, and dataset semantics |
| [System diagram](docs/diagrams/architecture.md) | End-to-end component and data flow |
| [Pipeline diagram](docs/diagrams/pipeline.md) | Success and invariant-failure sequence |
| [Ethereum diagram](docs/diagrams/ethereum.md) | RPC, raw chain data, plugins, and invariants |
| [Incident replay diagram](docs/diagrams/incident-flow.md) | Research-case replay and dataset export |
| [NixOS research workstation](docs/nixos.md) | Reproducible shell, storage boundary, generation, DuckDB, and benchmarks |
| [Release notes](docs/release.md) | Scope of v0.1.0 |

## Repository structure

```text
src/sentinel/
  config/       Environment-backed settings
  database/     SQLAlchemy engine setup
  models/       PostgreSQL schema models
  ingestion/    Shared fixture and Ethereum pipelines
  ethereum/     Provider-neutral RPC and ABI utilities
  protocols/    Protocol plugin interface and implementations
  quality/      Configurable source-data checks
  security/     Invariants, incidents, replay, features, and export
  reporting/    Static HTML, timeline, chart, and SVG report generation
  research/     Deterministic Parquet generation and DuckDB analysis
  cli/          Operational command-line interface

migrations/     Alembic migration history
tests/unit/     Isolated domain and CLI tests
tests/integration/ PostgreSQL-backed pipeline scenarios
configs/        Versioned, non-secret configuration
scripts/        Explicit local benchmark entry points
sql/            PostgreSQL analytics and DuckDB research queries
data/fixtures/  Deterministic ingestion scenarios
data/incidents/ Versioned security research cases
data/research/  Git-ignored external, generated, curated, and benchmark data
docs/           Concepts, diagrams, examples, and release notes
```

PostgreSQL separates ownership across five schemas: `metadata`, `raw`, `core`, `analytics`,
and `security`.

## Quality

Run the complete project checks:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run alembic check
```

PostgreSQL integration tests use:

```bash
SENTINEL_TEST_DATABASE_URL=postgresql+psycopg://sentinel:sentinel@localhost:5432/sentinel_test \
  uv run pytest -m integration
```

The current baseline is 88 passing tests. Tests use an injectable Ethereum RPC client;
they do not contact a public network.

## Project scope

Sentinel Warehouse is currently a research and portfolio platform, not a production custody,
trading, alerting, or vulnerability-scanning service. v0.1.0 intentionally includes no:

- ML models
- AI, LLM, or MCP agents
- frontend or public API
- continuous blockchain monitoring
- Kubernetes or cloud deployment layer

These boundaries keep the financial integrity model testable before higher-level automation is
introduced.

## License

A license has not yet been selected. The repository should not be treated as granting reuse,
modification, or redistribution rights until a `LICENSE` file is added.
