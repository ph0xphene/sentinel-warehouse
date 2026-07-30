# Core concepts

Sentinel Warehouse is built around one idea: financial correctness should be reproducible from
source evidence and explicit rules, not inferred from a final balance alone.

## Financial events

Financial events are the canonical language shared by every source.

A traditional ledger row, an ERC-20 transfer log, and a Uniswap pool event have different
source formats. They can still represent comparable state transitions. Normalization converts
those source records into a small extensible vocabulary such as `CREATE`, `MINT`, `BURN`,
`TRANSFER`, `DEPOSIT`, `WITHDRAWAL`, `FEE`, `INTEREST`, and `ADJUSTMENT`.

```text
Deposit
   |
Transfer
   |
Withdrawal
   v
Ordered state transitions
   v
Reconstructed balances
```

Each canonical event identifies its source system and external identity, occurrence time,
asset, participating accounts, amount, and structured metadata. Source-specific information
remains available in the immutable raw layer and event metadata.

Events are canonical because they provide:

- a stable contract between ingestion adapters and financial analysis;
- deterministic ordering for replay;
- source-independent balance reconstruction;
- lineage back to the original raw record;
- an extension point for new sources without rewriting the integrity engine.

State is derived by replaying canonical events in a stable order. For example:

```text
CREATE 1,000 USD -> account A
TRANSFER 100 USD: account A -> account B

Result:
account A = 900 USD
account B = 100 USD
```

The stored result can be compared with a reported balance snapshot. If the event-derived and
reported states disagree, the difference is evidence rather than an unexplained data defect.

## Invariants

A financial system is correct only while its defining rules continue to hold.

Sentinel expresses those rules as invariants: deterministic checks over candidate events and
reconstructed state. Global invariants apply regardless of source. Protocol plugins may add
rules that only make sense for their own financial mechanism.

Examples:

- balances cannot appear from nowhere;
- transfers preserve total value;
- accounts cannot cross below zero in a full-state reconstruction;
- transfer events have a source, destination, asset, and positive amount;
- reported balance snapshots match event-derived state;
- Uniswap reserves remain consistent with `Sync` observations;
- liquidity conservation holds across supported pool transitions.

Invariant execution happens before candidate events are committed to trusted core state. Every
result is stored with its batch, severity, affected records, and execution attempt. This is
separate from data-quality validation:

- **Quality checks** ask whether source data is complete, well-formed, and internally
  reconcilable.
- **Invariants** ask whether the financial behavior represented by that data is valid.

## Incidents

Failures are not discarded as transient pipeline errors.

When an invariant fails, Sentinel:

1. Persists the invariant execution result.
2. Creates or updates an auditable incident.
3. Attaches structured evidence for every affected record.
4. Marks the pipeline attempt failed without loading invalid core state.

An incident answers:

- **What happened?** The invariant type and human-readable summary.
- **Where?** The affected batch, protocol, event, account, or asset.
- **Why?** The failed financial rule and structured reason.
- **What is the evidence?** The exact affected records and attempt-scoped payloads.

Retries reuse the same logical investigation. If corrected source data later succeeds, active
incidents are resolved while their earlier evidence remains available.

Historical incident cases are different from detected runtime incidents. A case is a curated,
provenance-complete research definition containing an expected outcome and ordered attack flow.
Replaying it through the same pipeline verifies that Sentinel reproduces the expected financial
failure—or correctly produces no false positive for a control case.

## Investigations

Stored evidence is useful only when a human can understand the sequence and conclusion.
Sentinel therefore separates three responsibilities:

1. The **data layer** preserves source records and normalizes canonical events.
2. The **validation layer** reconstructs state, executes invariants, and creates incidents.
3. The **investigation layer** presents the existing timeline, state changes, invariant
   reasoning, and evidence without changing detection behavior.

The investigation layer generates static HTML. It does not query an API after generation and
does not contain executable JavaScript or external assets. Inline SVG provides two focused
views:

- a balance-delta chart answering which entities gained or lost value;
- a relationship graph connecting sources, assets, destinations, and findings.

For Ethereum-derived events, the visible timeline follows block number, transaction index,
and log index. Non-chain events use their occurrence time. `FAIL` and
`INSUFFICIENT_EVIDENCE` remain distinct so a report never presents incomplete analysis as a
successful proof.

## Why blockchain is a source, not the goal

Ethereum is useful because it provides:

- transparent public data;
- reproducible historical blocks and logs;
- explicit transaction ordering;
- complex financial state transitions;
- real protocol behavior suitable for integrity research.

Those properties make Ethereum an excellent source for testing Sentinel's model. They do not
make the architecture blockchain-specific.

The ingestion pipeline depends on canonical financial events and invariant contracts, not
provider SDKs or chain-specific objects. Traditional database fixtures and Ethereum logs enter
through different adapters, then share state reconstruction, global invariants, incidents, and
dataset production.

This source-independent boundary is intentional. Future payment systems, accounting ledgers, or
other event-driven financial sources can reuse the same integrity model without duplicating the
pipeline.

## Further reading

- [System architecture](diagrams/architecture.md)
- [Pipeline sequence](diagrams/pipeline.md)
- [Ethereum ingestion](diagrams/ethereum.md)
- [Incident research flow](diagrams/incident-flow.md)
- [Detailed architecture](architecture.md)
