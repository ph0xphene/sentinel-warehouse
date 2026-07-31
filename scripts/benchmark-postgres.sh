#!/usr/bin/env bash
set -euo pipefail

mode="${1:-}"
benchmark_database="${SENTINEL_BENCHMARK_DATABASE:-sentinel_benchmark}"
scale="${SENTINEL_PGBENCH_SCALE:-1}"
clients="${SENTINEL_PGBENCH_CLIENTS:-4}"
jobs="${SENTINEL_PGBENCH_JOBS:-2}"
seconds="${SENTINEL_PGBENCH_SECONDS:-30}"

case "$mode" in
  init)
    docker compose exec -T postgres dropdb \
      --username "${POSTGRES_USER:-sentinel}" \
      --if-exists "$benchmark_database"
    docker compose exec -T postgres createdb \
      --username "${POSTGRES_USER:-sentinel}" \
      "$benchmark_database"
    docker compose exec -T postgres pgbench \
      --username "${POSTGRES_USER:-sentinel}" \
      --initialize \
      --scale "$scale" \
      "$benchmark_database"
    ;;
  run)
    docker compose exec -T postgres pgbench \
      --username "${POSTGRES_USER:-sentinel}" \
      --client "$clients" \
      --jobs "$jobs" \
      --time "$seconds" \
      --progress 5 \
      "$benchmark_database"
    ;;
  clean)
    docker compose exec -T postgres dropdb \
      --username "${POSTGRES_USER:-sentinel}" \
      --if-exists "$benchmark_database"
    ;;
  *)
    echo "Usage: $0 {init|run|clean}" >&2
    echo "The benchmark database is separate from the Sentinel application database." >&2
    exit 2
    ;;
esac
