# Ingestion pipeline

Every source uses the same lifecycle after source-specific collection. Raw evidence is committed
before validation, while canonical loading, checkpoint advancement, and final success commit
together.

```mermaid
sequenceDiagram
    autonumber
    actor Source
    participant Pipeline
    participant Metadata
    participant Raw
    participant Normalizer
    participant Core
    participant Invariants
    participant Incidents

    Source->>Pipeline: Submit source data
    Pipeline->>Metadata: Start or resume logical batch
    Pipeline->>Raw: Persist immutable source records
    Pipeline->>Metadata: Record staged state
    Pipeline->>Normalizer: Produce canonical candidates
    Normalizer->>Pipeline: Financial events
    Pipeline->>Pipeline: Run configurable quality checks
    Pipeline->>Invariants: Reconstruct state and execute checks

    alt Quality and invariants pass
        Invariants-->>Pipeline: Valid state
        Pipeline->>Core: Commit financial events and balances
        Pipeline->>Metadata: Advance checkpoint and mark succeeded
        Pipeline-->>Source: Successful batch summary
    else Invariant violation
        Invariants-->>Pipeline: Failed invariant and affected records
        Pipeline->>Metadata: Persist invariant result and mark failed
        Pipeline->>Incidents: Create or update incident
        Pipeline->>Incidents: Attach structured evidence
        Pipeline-->>Source: Failed batch summary
    else Quality validation failure
        Pipeline->>Metadata: Persist quality result and mark failed
        Pipeline-->>Source: Failed batch summary
    end
```

Retrying the same failed logical batch preserves its identity and raw evidence. Re-running a
successful logical batch is idempotent.

