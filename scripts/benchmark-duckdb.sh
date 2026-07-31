#!/usr/bin/env bash
set -euo pipefail

dataset="${1:?Usage: $0 DATASET_DIRECTORY [RUNS]}"
runs="${2:-5}"

uv run --group research sentinel research benchmark \
  "$dataset" \
  --runs "$runs" \
  --output "data/research/benchmarks/$(basename "$dataset")-duckdb.json"
