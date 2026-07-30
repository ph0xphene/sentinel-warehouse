# Ethereum ingestion

Ethereum collection is bounded, finality-aware, and downstream-compatible with the same
financial event and incident pipeline used by traditional sources.

```mermaid
flowchart TB
    rpc["Ethereum JSON-RPC"]
    range["Finalized block-range collector"]
    blocks["Blocks"]
    txlogs["Transactions and logs"]
    rawblocks["raw.ethereum_blocks"]
    rawlogs["raw.ethereum_transactions / transfers / logs"]
    canonical["Canonical-chain selection"]
    registry["Protocol plugin registry"]
    detect["detect()"]
    normalize["normalize()"]
    pinvariants["invariants()"]
    events["core.financial_events"]
    global["Global invariant engine"]
    incidents["Incidents and evidence"]

    rpc --> range
    range --> blocks
    range --> txlogs
    blocks --> rawblocks
    txlogs --> rawlogs
    rawblocks --> canonical
    rawlogs --> canonical
    canonical --> registry
    registry --> detect
    detect --> normalize
    normalize --> events
    events --> global
    events --> pinvariants
    global -->|"Violation"| incidents
    pinvariants -->|"Protocol violation"| incidents
```

The current plugins cover ERC-20 transfers and Uniswap V2 events. Chain observations are never
deleted; reorganization handling marks orphaned observations and derived events non-canonical,
then rewinds the durable checkpoint to a known common ancestor.

See [Detailed architecture: live Ethereum ranges](../architecture.md#milestone-7-live-ethereum-ranges).

