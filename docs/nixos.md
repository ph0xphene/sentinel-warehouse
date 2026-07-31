# NixOS research workstation

Sentinel's local research environment keeps the transactional application database in
PostgreSQL and large analytical inputs in a file-based Parquet lake. It is designed for
repeatable workstation experiments, not for internet-facing deployment.

## Host prerequisites

Enable Docker and Nix flakes in the host configuration:

```nix
{
  nix.settings.experimental-features = [ "nix-command" "flakes" ];
  virtualisation.docker.enable = true;
  users.users.<your-user>.extraGroups = [ "docker" ];
}
```

Apply the NixOS configuration and sign in again so the Docker group membership takes effect:

```bash
sudo nixos-rebuild switch
```

The repository flake supports `x86_64-linux` and `aarch64-linux`. It provides Python 3.12,
uv, PostgreSQL client tools, Docker Compose, DuckDB, jq, and hyperfine:

```bash
nix develop
uv sync --group research
```

`flake.lock` pins the Nix package set. `uv.lock` independently pins the Python environment.

## Storage boundary

The two storage systems have deliberately different responsibilities:

| Storage | Location | Responsibility |
| --- | --- | --- |
| Application database | Docker volume `sentinel_postgres_data` | Pipeline metadata, raw evidence, canonical entities, invariant results, incidents, and lightweight analytics views |
| Research lake | `data/research/` or `SENTINEL_RESEARCH_ROOT` | External, generated, curated, and benchmark Parquet artifacts |

Do not mount the research lake into PostgreSQL or load a large synthetic corpus into the
application database by default. This keeps transactional migration and recovery behavior
independent from analytical scan workloads. The lake contents are ignored by Git; generator
manifests make local datasets identifiable and reproducible.

For a larger local disk, point the lake at a dedicated filesystem:

```bash
export SENTINEL_RESEARCH_ROOT=/mnt/research/sentinel
mkdir -p "$SENTINEL_RESEARCH_ROOT"/{external,generated,curated,benchmarks,tmp}
```

## PostgreSQL

Create local configuration and start the database:

```bash
cp .env.example .env
docker compose up -d postgres
docker compose ps
uv run sentinel db migrate
uv run sentinel db status
```

The service:

- binds only to `127.0.0.1`;
- persists checksummed data in a named Docker volume;
- uses a health check, graceful shutdown, shared memory, and higher file limits;
- mounts `configs/postgres/postgresql.research.conf`;
- creates `sentinel_test` on first initialization for integration tests.

Initialization scripts only run when PostgreSQL creates a new empty volume. An existing
volume is never deleted automatically. Create the test database manually when upgrading an
older local volume:

```bash
docker compose exec postgres createdb \
  --username sentinel \
  --owner sentinel \
  sentinel_test
```

The checked-in PostgreSQL values are conservative workstation defaults. Measure before
changing `shared_buffers`, `work_mem`, `effective_cache_size`, or WAL limits; `work_mem` can
be allocated more than once per query and per concurrent connection.

Back up the application database without including research files:

```bash
mkdir -p backups
docker compose exec -T postgres pg_dump \
  --username sentinel \
  --format custom \
  sentinel > backups/sentinel.dump
```

## Deterministic Parquet generation

Generation is always explicit. The default command writes only 10,000 events:

```bash
uv run sentinel research generate
```

Choose parameters and an output path for a named experiment:

```bash
uv run sentinel research generate \
  --output data/research/generated/transfer-study \
  --rows 250000 \
  --accounts 10000 \
  --rows-per-file 50000 \
  --seed 42
```

The generator uses formula-based values, a fixed UTC epoch, exact Decimal128 amounts, stable
file names, Zstandard compression, and SHA-256 file hashes. It refuses to overwrite a
non-empty directory. Repeating the same parameters in two empty destinations produces the
same dataset ID and file hashes.

Estimate capacity before intentionally creating a multi-million-row experiment. Sentinel
never creates one during setup, tests, migrations, or `nix develop`.

## DuckDB workflow

Inspect Parquet directly, without importing it:

```bash
uv run --group research sentinel research inspect \
  data/research/generated/transfer-study \
  --threads 8 \
  --memory-limit 8GB
```

Open the DuckDB CLI for ad hoc SQL:

```bash
duckdb
```

Then run queries over the file glob:

```sql
SELECT event_date, asset, count(*) AS events, sum(amount) AS volume
FROM read_parquet('data/research/generated/transfer-study/events/*.parquet')
GROUP BY event_date, asset
ORDER BY event_date, asset;
```

The reusable example is in `sql/duckdb/research_activity.sql`.

## Analytics schema

Migration `20260730_0013` prepares two read-only PostgreSQL views:

- `analytics.canonical_event_flows` exposes canonical events with external account and asset
  identifiers;
- `analytics.daily_asset_activity` aggregates canonical event counts and gross amount by
  day, source, asset, and event type.

Run `sql/analytics/event_activity.sql` with `psql` for a bounded application-data report.
Keep broad, exploratory scans over large research corpora in DuckDB.

## Benchmarks

DuckDB benchmarks execute a fixed query suite, perform one warm-up, and write machine and
dataset metadata with minimum, median, and p95 durations:

```bash
scripts/benchmark-duckdb.sh \
  data/research/generated/transfer-study \
  5
```

The PostgreSQL benchmark uses a dedicated `sentinel_benchmark` database so it cannot mix
pgbench tables with application state. Initialization, execution, and cleanup are separate,
explicit commands:

```bash
SENTINEL_PGBENCH_SCALE=10 scripts/benchmark-postgres.sh init
SENTINEL_PGBENCH_CLIENTS=8 SENTINEL_PGBENCH_SECONDS=60 \
  scripts/benchmark-postgres.sh run
scripts/benchmark-postgres.sh clean
```

For a query-plan benchmark against Sentinel application data:

```bash
docker compose exec -T postgres psql \
  --username sentinel \
  --dbname sentinel \
  < sql/benchmarks/postgres_event_activity.sql
```

Record the dataset manifest, Git revision, Nix lock revision, command, thread count, memory
limit, and storage device alongside any published result. Workstation benchmark numbers are
comparative measurements, not production capacity guarantees.
