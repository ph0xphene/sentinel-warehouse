# v0.1.0 — Financial Integrity Foundation

## Summary

Sentinel Warehouse v0.1.0 is the initial public foundation release of a financial integrity
and security research platform.

The release demonstrates how traditional financial records and Ethereum activity can share a
single auditable pipeline: immutable ingestion, canonical event normalization, deterministic
state reconstruction, invariant validation, incident preservation, and reproducible dataset
export.

## Included

### Ingestion engine

- Immutable raw persistence
- Batch lineage and lifecycle history
- Configurable quality checks
- Durable source checkpoints
- Retry-safe, idempotent logical batches
- Deterministic failure injection for integration testing

### Financial event model

- Canonical, source-independent financial events
- Account, asset, transaction, and balance entities
- Event-derived balance reconstruction
- Snapshot reconciliation

### Invariant validation

- Balance conservation
- No negative balances
- Event completeness
- Balance-snapshot matching
- Protocol-specific invariant registration
- Checker-owned invariant scope and supply authority configuration
- Explicit insufficient-evidence results for partial history

### Incident framework

- Auditable incidents and structured evidence
- Incident status lifecycle
- Retry-aware resolution
- Isolated live, replay, and fixture finding origins
- Reproducible historical case import and replay
- Normalized attack taxonomy and rich provenance

### Ethereum support

- Deterministic ERC-20 transfer fixtures
- Bounded historical JSON-RPC ingestion
- Finality-aware range processing
- Canonical block and log history
- Chain-reorganization recovery
- Chain-native block/transaction/log ordering
- Explicit unsupported-analysis failure without checkpoint advancement
- ERC-20 and Uniswap V2 protocol plugins

### Security dataset pipeline

- Deterministic case-level feature extraction
- Sequence-aware features
- Dataset provenance validation
- Versioned, ML-ready Parquet export
- Exact Decimal128 financial feature export

### Investigation reporting

- Self-contained offline HTML reports for cases and incidents
- Chronological event and invariant-failure timelines
- Before/after state with exact balance deltas
- Inline SVG balance and relationship visualizations
- Structured evidence and origin presentation

## Release versions

| Component | Version |
| --- | --- |
| Application | `0.1.0` |
| Dataset contract | `1.1.0` |
| Feature extraction | `3.0.0` |
| Parquet schema | `3` |
| Latest database migration | `20260730_0013` |

## Verification baseline

The release was prepared against:

- Python 3.12
- PostgreSQL 17 via Docker Compose
- 88 passing unit and PostgreSQL integration tests
- Ruff lint and format checks
- Alembic model-drift and migration-head checks

## Deliberate exclusions

v0.1.0 does not contain ML models, AI/LLM/MCP agents, a frontend, public APIs, continuous
monitoring, new protocol families beyond the existing ERC-20 and Uniswap V2 support, or a
deployment platform.

## License status

No license has been selected yet. Add a root `LICENSE` file before representing the repository
as granting open-source reuse or redistribution rights.
