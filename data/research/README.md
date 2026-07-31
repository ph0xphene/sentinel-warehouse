# Local research lake

This directory is intentionally separate from the Sentinel PostgreSQL application database.

| Directory | Purpose |
| --- | --- |
| `external/` | Manually acquired source datasets; never modified by generators |
| `generated/` | Deterministic synthetic Parquet datasets |
| `curated/` | Reproducible derived Parquet datasets |
| `benchmarks/` | Local benchmark result JSON |
| `tmp/` | Disposable DuckDB spill and intermediate files |

Generated data is not committed. Each Sentinel-generated dataset contains a deterministic
`manifest.json` with generator parameters and per-file SHA-256 checksums.
