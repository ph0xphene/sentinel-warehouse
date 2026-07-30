# Architecture

Sentinel Warehouse separates data by lifecycle and responsibility:

For a concise first read, start with [Core concepts](concepts.md) and the
[system architecture diagram](diagrams/architecture.md). This document is the detailed
engineering reference.

| Schema | Purpose |
| --- | --- |
| `metadata` | Batch execution, lineage, and pipeline operational state |
| `raw` | Immutable, source-aligned records |
| `core` | Validated and normalized financial business entities |
| `analytics` | Derived metrics and reporting-ready datasets |
| `security` | Invariant executions, incidents, evidence, research cases, and datasets |

Data will flow from `raw` to `core` to `analytics`. The `metadata` schema records every
ingestion attempt and provides the lineage needed to reproduce or investigate results.
Security analysis consumes trusted core entities without changing their ownership.

The Python package follows the same separation: configuration and database primitives are
shared infrastructure, while ingestion and quality modules contain domain workflows. New
abstractions should be introduced only when a concrete second use case requires them.

## Milestone 1 ingestion

```text
JSON fixture
    |
    v
metadata.ingestion_batches + raw.financial_records
    |
    v
required fields / duplicate IDs / negative amounts / reconciliation
    |
    +--> metadata.quality_results
    |
    v (only when every check passes)
core.accounts / core.assets / core.transactions / core.balances
```

Raw records are committed before validation so a failed batch remains reproducible.
Quality results commit after validation, before core loading begins. `rows_loaded` records
the number of normalized core rows, while failed batches retain a value of zero.

Transactions represent an asset movement between two accounts. This intentionally neutral
model works for conventional sources while leaving room for other source types later.
Balances are point-in-time account and asset positions. Fixture-only `opening_amount`
values are used to verify:

```text
closing balance = opening balance + inbound transactions - outbound transactions
```

## Milestone 2 pipeline controls

Each fixture is identified by its source name and SHA-256 checksum. That pair is the
logical batch key:

- A new key creates and stages one batch.
- A previously failed key retries the same batch and increments `attempt_count`.
- A previously successful key returns its stored result without writing raw or core rows.
- An active key is rejected to prevent concurrent execution of the same batch.

The batch state machine is deliberately small:

```text
running -> staged -> validating -> loading -> invariant_checking -> succeeded
    |         |           |           |                |
    +---------+-----------+-----------+----------------+-> failed
                                                           |
                                                           +-> running (retry)
```

Every transition is appended to `metadata.batch_state_history` with its attempt number.
The current state remains on `metadata.ingestion_batches` for efficient operational
queries. Raw staging commits before validation. Canonical candidates are checked before
core commit; core loading, checkpoint advancement, and the final `succeeded` transition
commit together.

### Checkpoints

`metadata.source_checkpoints` stores one current source position, its successful batch,
and a monotonically increasing version. Incremental fixtures declare `previous_checkpoint`
and `checkpoint`. The previous value must match stored state; otherwise the batch fails a
blocking `checkpoint_continuity` result. Failed and injected runs never advance a checkpoint.

### Configurable quality

Quality policy is JSON-based. Each known check can be enabled or disabled and marked as
blocking or observational. The repository default is
`configs/quality/default.json`. Disabling a check does not remove database constraints;
PostgreSQL remains the final integrity boundary.

### Failure injection

`FailureInjector` can stop execution at stable boundaries after raw staging, around
validation, invariant execution, or core loading. Injected failures use normal failure
transitions and leave durable metadata, making retry behavior deterministic without
embedding test-only branches in the pipeline.

## Milestone 3 canonical event model

Source transactions and explicit source events normalize into
`core.financial_events`. Event type is stored as text so new domain-specific types can be
introduced without changing the physical table. The initially supported vocabulary is
`CREATE`, `MINT`, `BURN`, `TRANSFER`, `DEPOSIT`, `WITHDRAWAL`, `FEE`, `INTEREST`, and
`ADJUSTMENT`.

For a source's first checkpoint, opening balances become authorized `CREATE` events.
Later checkpoints contribute only their new events. This makes current state reproducible
by sorting the complete event history and applying each debit or credit:

```text
Raw data
    |
    v
Financial Events
    |
    v
State reconstruction
    |
    v
Invariant validation
    |
    v
Security analytics
```

For example, an authorized opening event credits account A with 1,000 USD. A subsequent
100 USD transfer debits A and credits B, reconstructing A = 900 and B = 100.

### Invariant execution

The invariant engine currently executes:

- `balance_conservation`: transfer supply deltas must be zero; non-zero supply changes
  require explicit authorization.
- `no_negative_balances`: no account may become negative at any event boundary.
- `event_completeness`: transfers require both accounts, an asset, and a positive amount.
- `balance_snapshot_match`: reported balances must match event-derived balances.

Each execution is written to `security.invariants` with severity, description, result,
affected records, batch ID, and attempt number. These records are independent from
`metadata.quality_results`: quality validates source shape, while invariants validate
financial behavior.

Candidate events are evaluated before they enter `core`. A blocking violation transitions
the batch from `invariant_checking` to `failed`, preserves raw and invariant evidence, and
does not advance the checkpoint. Successful events, balance snapshots, checkpoint state,
and the terminal batch transition commit atomically.

## Milestone 4 incident investigation

Failed invariants now become auditable records in `security.incidents`. The end-to-end
investigation path is:

```text
Source
  |
  v
Raw
  |
  v
Core event candidates
  |
  v
State reconstruction
  |
  v
Invariant engine
  |
  v
Incident detection
  |
  v
Investigation
```

One incident is maintained for each batch and failed invariant. Its type is the invariant
name, its severity comes from the invariant definition, and its summary explains the
failure. `security.incident_evidence` stores every affected record as a structured payload
with an affected entity and evidence type.

Incident statuses are `OPEN`, `INVESTIGATING`, `RESOLVED`, and `IGNORED`. A repeated failed
attempt reuses the incident and appends attempt-scoped evidence instead of creating a
duplicate investigation. If the same logical batch later succeeds, active incidents become
`RESOLVED` in the same transaction as event loading and checkpoint advancement. Incidents
marked `IGNORED` are not changed automatically.

Operators can inspect the investigation ledger without direct SQL:

```bash
uv run sentinel incident list
uv run sentinel incident show <incident-id>
```

## Milestone 5 Ethereum source adapter

Ethereum is the first external source adapter and is intentionally limited to deterministic
ERC-20 `Transfer` logs. It extends the source boundary without changing the downstream
pipeline:

```text
Ethereum transaction + ERC-20 log fixture
                 |
                 v
raw.ethereum_transactions
raw.ethereum_transfers
                 |
                 v
Ethereum normalization adapter
                 |
                 v
core.financial_events (TRANSFER, source_system = ethereum)
                 |
                 v
State reconstruction -> invariants -> incidents
```

`raw.ethereum_transactions` retains transaction envelopes, execution status, block
coordinates, addresses, and source metadata. `raw.ethereum_transfers` retains the log
identity and unscaled integer token amount. Both include batch lineage in their composite
keys, so a replay can be preserved as immutable source evidence.

The adapter lowercases addresses and hashes, applies the token's declared decimal precision,
and uses `<tx_hash>:<log_index>` as the canonical event external ID. Successful transaction
logs become `TRANSFER` events. Failed transaction envelopes remain in raw but do not produce
financial events.

Replay detection uses the existing duplicate-external-ID quality check against canonical
event history. A replayed log therefore remains visible in raw while core state, checkpoint
state, and incidents remain unchanged. Impossible token state follows the same invariant and
incident path as traditional financial data.

This milestone does not include RPC access, block crawling, chain reorganization handling,
contract discovery, or event types beyond ERC-20 `Transfer`.

## Milestone 6 protocol plugins

Protocol-specific interpretation now sits behind one small interface. The ingestion
pipeline does not import Ethereum protocols or branch on protocol names:

```text
Ethereum fixture
      |
      v
Protocol registry
      |
      v
detect() -> normalize() -> canonical events
                             |
                             v
                  Shared ingestion pipeline
                             |
             +---------------+----------------+
             |                                |
             v                                v
       Global invariants              plugin.invariants()
             |                                |
             +---------------+----------------+
                             |
                             v
                 Incidents + protocol name
```

Every plugin implements three operations:

- `detect(source)` determines whether the plugin owns a source payload.
- `normalize(source)` produces protocol-neutral canonical events, account addresses, and
  immutable raw records.
- `invariants(events, source)` evaluates protocol rules after the global invariant engine.

The registry selects exactly one plugin. Adding a protocol therefore requires a plugin and
registry entry, while raw staging, quality validation, batch lifecycle, retry behavior,
checkpoints, core loading, and incident creation remain shared. The original ERC-20 transfer
behavior is retained as a compatibility plugin.

### Uniswap V2

The first protocol-aware plugin supports the four pool event families:

| Uniswap event | Canonical representation |
| --- | --- |
| `Mint` | One authorized `MINT` event for each reserve asset |
| `Burn` | One authorized `BURN` event for each reserve asset |
| `Swap` | Pool inputs as `DEPOSIT`; pool outputs as `WITHDRAWAL` |
| `Sync` | Zero-amount `ADJUSTMENT` observations carrying reported reserves |

Deposits and withdrawals represent the bounded pool view: the plugin can reconstruct pool
reserves without assuming that external trader balances are present in the fixture. Every
canonical event retains its protocol name, source event family, transaction hash, and log
index in structured metadata.

After global checks pass, Uniswap registers:

- `reserve_consistency`: reconstructed pool balances must match the latest `Sync` reserves.
- `liquidity_conservation`: the reserve product after a swap's next `Sync` must not decrease.

Protocol failures use the existing incident and evidence workflow. Both invariant results
and incidents store `protocol_name`, so investigators can filter violations without parsing
evidence payloads. A successful retry resolves the prior incident through the same shared
batch lifecycle used by conventional and ERC-20 sources.

The adapter remains fixture-driven. It does not connect to a node, discover contracts, or
implement chain reorganization handling.

## Milestone 7 live Ethereum ranges

Live ingestion is a bounded collection adapter in front of the existing protocol and
financial pipeline:

```text
Ethereum JSON-RPC
        |
        v
Observed block and log history
        |
        v
Canonical-chain filtering
        |
        v
Protocol plugin normalization
        |
        v
Financial events
        |
        v
Invariants and incidents
```

The asynchronous RPC interface exposes only `eth_chainId`, `eth_getBlockByNumber`, and
`eth_getLogs`. Its HTTP implementation has a request timeout and bounded exponential retry;
tests inject an in-memory fake instead of contacting a public provider. Provider SDK types
do not cross the source boundary.

### Finality and range collection

The command accepts one contract and a finite inclusive range. Requests are divided into
chunks no larger than `SENTINEL_ETHEREUM_MAX_BLOCK_RANGE`. Before collection, the adapter
verifies that the provider's chain ID matches `SENTINEL_ETHEREUM_CHAIN_ID`.

The finalized ingestion boundary is:

```text
latest observed block - configured confirmation depth
```

If `--to-block` exceeds that boundary, the effective range is truncated and the CLI reports
both values. The first import for a contract requires `--from-block`; later runs may omit it
to start at the stored checkpoint plus one. There is no polling loop, websocket subscription,
or daemon mode.

### Observed versus canonical data

`raw.ethereum_blocks` records every fetched block header with chain ID, number, hash, parent
hash, timestamp, batch lineage, and a canonical flag. `raw.ethereum_logs` preserves every
returned log, including unknown topics. A block number is never treated as immutable identity:
logs, decoded transfers, and financial events carry chain ID, block number, and block hash.

Observed records are never physically deleted. `canonical = false` means the source evidence
was observed but no longer belongs to the selected chain. Balance reconstruction and duplicate
checks read canonical financial events only. A partial historical range does not claim full
account pre-state, so the global negative-balance invariant excludes live events marked
`state_scope = partial_history`; conservation, completeness, protocol invariants, and all
fixture-based full-state checks continue to run.

Unknown log signatures remain canonical raw observations and produce no financial events.
ABI decoding stays in the ERC-20 or Uniswap plugin, with only static-word helpers shared under
`sentinel.ethereum`.

### Checkpoint transaction

One checkpoint is keyed by chain ID and normalized contract source identity. It stores the
last successful block number and hash in addition to the generic checkpoint value. The
checkpoint advances in the same transaction as core loading and the terminal `succeeded`
transition. Raw staging, decoding, validation, or invariant failure leaves it unchanged.
Re-running an identical explicit range returns its original successful batch without adding
observations.

### Reorganization handling

Before a resume, the adapter fetches the checkpoint block and compares hashes. On divergence
it searches backwards through locally observed canonical headers, bounded by
`SENTINEL_ETHEREUM_REORG_LOOKBACK`.

For a shallow reorganization, one database transaction:

1. Marks orphaned block observations and dependent raw records non-canonical.
2. Marks derived financial events from those block hashes non-canonical.
3. Rewinds the checkpoint to the common ancestor.
4. Allows the caller's finite range to replay from the ancestor plus one.

The CLI prints the detected height, common ancestor, and orphan count. Repeating recovery is
safe because already-orphaned records are excluded from subsequent canonical updates.

If no common ancestor is found within the lookback, ingestion stops, the checkpoint is not
changed, and a critical `ethereum_deep_reorganization` operational incident is attached to
the last successful batch with structured evidence. An operator must investigate or expand
the configured lookback; the adapter never silently selects a chain.

### Current live-source limitations

- Only Ethereum mainnet is configured by default.
- Collection covers one contract and an explicit historical range per invocation.
- There is no transaction-receipt retrieval, mempool data, beacon finality, or chain daemon.
- The deliberately small RPC interface cannot call token metadata or Uniswap pair getters.
  Live ERC-20 amounts therefore remain integer base units (`decimals = 0`), and live Uniswap
  reserve assets use deterministic `token0`/`token1` slot identifiers. Deterministic fixtures
  continue to support declared token addresses and decimals.

All default tests run through `FakeEthereumRPC`; public network access is never required.

## Milestone 8 incident reconstruction

Historical incident research is modeled separately from detected runtime incidents:

```text
Incident case JSON
        |
        v
security.incident_cases + ordered attack_flows
        |
        v
Embedded deterministic replay fixture
        |
        v
Existing raw -> core -> invariant -> incident pipeline
        |
        v
Expected outcome <-> actual outcome comparison
```

`security.incident_cases` stores the research identity, protocol label, category, severity,
reference transactions, narrative, and a self-contained replay definition. Persisting the
definition rather than a local path means the database retains the exact input and expected
outcome used by later runs.

`security.attack_flows` stores human-readable steps under a unique `(case_id, step_number)`.
Steps are always displayed in numeric order. `event_id` is nullable because the pipeline
deliberately rejects invariant-violating candidate events before they enter
`core.financial_events`. A control or successful replay can link its flow step to the
committed canonical event.

### Case fixture contract

Each JSON case contains:

- `case`: metadata and external transaction references.
- `attack_flow`: ordered actions and explanations.
- `replay.fixture`: a normal deterministic financial fixture accepted by the shared pipeline.
- `replay.expected`: terminal status plus exact failed-invariant and incident-type sets.
- Optional `flow_event_external_ids`: successful-event links keyed by step number.

Imports validate required fields and step uniqueness. Re-importing the same case updates its
metadata and replaces its flow steps in one transaction. Replay is retry-safe and idempotent
because its embedded fixture uses the existing source/checksum batch identity.

The comparison succeeds only when actual status, failed invariant names, and incident types
exactly match the case expectation. A case whose expected outcome is `failed` therefore
returns a successful research result when the predicted incident is reproduced. Control
cases expect `succeeded` with empty violation sets and guard against false positives.

Operators use:

```bash
uv run sentinel case import data/incidents/euler_style_accounting_failure.json
uv run sentinel case list
uv run sentinel case show <case-id>
uv run sentinel case replay <case-id>
```

The included Euler-style scenario is a deterministic accounting analogue, not a claim to
reproduce a specific protocol implementation byte-for-byte. This layer is a reproducible
research framework; it does not discover vulnerabilities, monitor live contracts, or add a
new protocol adapter.

## Milestone 9 security dataset

Dataset construction is a deterministic projection of validated case replays:

```text
Labeled incident case
        |
        v
Idempotent replay + actual invariant outcomes
        |
        v
security.incident_features
        |
        v
Wide case-level projection
        |
        v
Zstandard-compressed Parquet
```

### Labels

`security.attack_patterns` is a curated label dictionary with a stable ID, name, category,
and description. Each case references one label. Labels are declared in the versioned case
JSON and upserted during case import; they are not inferred from detected incidents. This
keeps positive and negative/control labels explicit and reviewable.

The two initial labels are:

- `Unbacked balance creation`: an accounting violation that increases supply without an
  authorized source or offsetting debit.
- `Benign conserved transfer`: a negative-label control that reconciles without invariant
  failures.

### Long-form feature store

`security.incident_features` has one row per `(case_id, feature_name)`. A check constraint
requires exactly one numeric or categorical value. Extraction replays the case, rejects
unlabeled cases, and atomically replaces its complete feature set:

| Feature | Type | Definition |
| --- | --- | --- |
| `number_of_events` | Numeric | Explicit transaction and event records in the replay fixture |
| `number_of_accounts` | Numeric | Accounts declared by the replay fixture |
| `affected_assets` | Categorical | Sorted, pipe-delimited assets touched by non-opening events |
| `transferred_volume` | Numeric | Sum of absolute non-opening canonical candidate amounts |
| `balance_delta` | Numeric | Net tracked supply change from non-opening events |
| `failed_invariants` | Categorical | Sorted failed invariant names, or `none` |
| `protocol_type` | Categorical | Protocol label stored on the research case |
| `event_sequence_length` | Numeric | Number of ordered attack-flow steps |

Numeric values remain exact `NUMERIC(38,18)` in PostgreSQL. Asset and invariant sets are
sorted before serialization, so repeated extraction of an unchanged case produces the same
feature values.

### Parquet export

`sentinel dataset export` extracts every imported case and pivots the long-form features to
one row per case. The fixed schema contains:

- case identity, protocol, category, severity, and reference transactions;
- attack-pattern ID, name, category, and description;
- expected replay status and a replay-match quality flag;
- five numeric and three categorical feature columns.

Export stops if any replay differs from its stored expectations. Output uses Parquet with
Zstandard compression, dictionary encoding, statistics, and schema metadata. The default
path is `data/exports/security_incidents.parquet`.

```bash
uv run sentinel case features <case-id>
uv run sentinel dataset export
uv run sentinel dataset export --output data/exports/experiment.parquet
```

This is a dataset-production boundary only. It does not train, score, select, or deploy an
ML model, and it introduces no LLM or MCP behavior.

## Milestone 10 security incident corpus

The corpus framework separates taxonomy, case provenance, deterministic extraction, and
versioned artifacts:

```text
Category -> Subcategory -> Attack pattern -> Incident case
                                               |
                                               v
                                  Replay + sequence features
                                               |
                                               v
                                    Corpus validation gate
                                               |
                                               v
                                     Versioned Parquet
```

### Normalized taxonomy

`security.attack_categories` defines top-level research categories and descriptions.
`security.attack_subcategories` belongs to exactly one category and captures a more specific
failure class. `security.attack_patterns` remains the supervised label dictionary and now
links to one subcategory.

Case JSON declares all three levels. Import uses stable IDs and upserts taxonomy descriptions
transactionally with the case, pattern, and ordered flow. Category and subcategory labels are
therefore queryable without parsing case JSON or relying on free-text naming conventions.

### Case provenance

Incident cases now persist:

- protocol and chain;
- affected contract identifiers;
- attacker addresses, which may be empty for a negative control;
- reference transactions;
- external reference URIs;
- confidence level (`low`, `medium`, or `high`);
- the existing description and self-contained replay definition.

New imports require non-empty chain, affected contracts, reference transactions, and external
references. Empty attacker-address arrays are valid for benign controls. Migration columns
remain nullable so existing databases can upgrade safely, but corpus validation rejects
legacy rows until they are re-imported with complete provenance.

### Sequence-aware extraction

Extraction version `2.0.0` adds:

| Feature | Type | Definition |
| --- | --- | --- |
| `event_type_sequence` | Categorical | Time-ordered explicit event types joined with `>` |
| `unique_contracts_count` | Numeric | Distinct affected contract identifiers on the case |
| `time_window_duration` | Numeric | Seconds between the first and last explicit event |
| `asset_transition_graph_size` | Numeric | Unique `(from, to, asset)` transition edges |
| `invariant_failure_category` | Categorical | Stable category mapped from failed invariants |

Opening-balance events are excluded from sequence, duration, graph, and movement features.
Ties are ordered by canonical external ID, and set-valued results are sorted before
serialization.

### Validation gate

`sentinel dataset validate` runs before every export and verifies:

1. Every case has an attack-pattern label linked through subcategory and category.
2. Required provenance is present.
3. Replay matches the stored expected status, invariant failures, and incident types.
4. Two consecutive extractions produce identical feature tuples.
5. The complete versioned feature-name set is present.

Export is blocked when any issue is found. The validator reports cases checked and
case-specific errors in plain terminal output.

### Dataset versions

Every Parquet artifact carries schema metadata:

- `dataset_version`: semantic version of the corpus release contract;
- `extraction_version`: feature-definition version;
- `schema_version`: physical Parquet schema version;
- `generated_at`: UTC generation timestamp;
- `dataset`: stable dataset identifier.

The current versions are dataset `1.0.0`, extraction `2.0.0`, and schema `2`. The wide schema
also includes rich provenance plus category, subcategory, and attack-pattern labels.

```bash
uv run sentinel dataset validate
uv run sentinel dataset export
```

This framework prepares reusable research data only. It does not train models, classify new
events, invoke LLM/MCP systems, or expose a frontend.
