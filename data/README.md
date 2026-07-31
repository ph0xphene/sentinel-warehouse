# Data ownership

Sentinel keeps application state and research-scale datasets separate.

- PostgreSQL stores operational metadata, raw ingestion evidence, canonical core state,
  invariant results, incidents, and small analytics views. Its files live in the Docker
  volume `sentinel_postgres_data`, not in this directory.
- `fixtures/` and `incidents/` are small, version-controlled deterministic inputs.
- `exports/` contains generated application artifacts such as the incident corpus.
- `research/` is a local Parquet lake for larger generated or externally acquired datasets.

Research dataset contents are ignored by Git. Only directory markers and documentation are
tracked.
