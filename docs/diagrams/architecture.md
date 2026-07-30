# System architecture

Sentinel separates source collection, immutable evidence, canonical financial semantics,
integrity checks, and research outputs. Ethereum is one source adapter; it does not define the
core domain.

```mermaid
flowchart TB
    subgraph sources["Financial sources"]
        traditional["Traditional financial data"]
        ethereum["Ethereum JSON-RPC"]
    end

    subgraph ingestion["Source ingestion"]
        batches["Batch lifecycle, lineage, checkpoints, retries"]
        plugins["Source adapters and protocol plugins"]
    end

    raw["Raw immutable layer"]
    events["Canonical financial event normalization"]
    state["State reconstruction"]
    invariants["Global and protocol invariant engine"]
    incidents["Incident management and evidence"]
    dataset["Versioned security research dataset"]
    future["Future ML / AI research"]

    traditional --> batches
    ethereum --> batches
    ethereum --> plugins
    batches --> raw
    plugins --> raw
    raw --> events
    events --> state
    state --> invariants
    invariants -->|"Violation"| incidents
    invariants -->|"Valid state"| dataset
    incidents --> dataset
    dataset -.-> future

    classDef future fill:#f5f5f5,stroke:#888,stroke-dasharray:5 5,color:#555;
    class future future;
```

The dashed final node is a future consumer of the dataset, not functionality included in
v0.1.0.

Related documentation:

- [Concepts](../concepts.md)
- [Detailed architecture](../architecture.md)
- [Pipeline sequence](pipeline.md)
- [Ethereum source flow](ethereum.md)
- [Incident replay flow](incident-flow.md)

