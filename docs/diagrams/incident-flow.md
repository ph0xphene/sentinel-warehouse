# Incident research flow

Historical cases are versioned research inputs. Replay uses the production-style ingestion and
invariant path rather than a separate simulation engine.

```mermaid
flowchart TB
    fixture["Incident case JSON"]
    case["Incident case + taxonomy + provenance"]
    flow["Ordered attack flow"]
    replay["Replay through ingestion pipeline"]
    actual["Actual batch, invariants, and incidents"]
    expected["Expected status, invariants, and incidents"]
    comparison{"Expected = actual?"}
    features["Deterministic feature extraction"]
    validation["Dataset validation"]
    export["Versioned Parquet export"]
    rejected["Export blocked with case-specific issues"]

    fixture --> case
    fixture --> flow
    case --> replay
    flow --> replay
    replay --> actual
    case --> expected
    actual --> comparison
    expected --> comparison
    comparison -->|"Yes"| features
    comparison -->|"No"| rejected
    features --> validation
    validation -->|"Valid"| export
    validation -->|"Invalid"| rejected
```

Validation also checks label completeness, provenance, the versioned feature set, and repeat
extraction determinism.

See [Concepts: incidents](../concepts.md#incidents) and
[Dataset validation example](../examples/dataset-validation.txt).

