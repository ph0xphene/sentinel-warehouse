import json
import math
import platform
import re
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Any

MEMORY_LIMIT_PATTERN = re.compile(r"^[1-9][0-9]*(?:MB|GB|TB)$", re.IGNORECASE)


class ResearchDependencyError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResearchInspection:
    dataset: Path
    event_rows: int
    unique_accounts: int
    unique_assets: int
    total_volume: Decimal
    first_event: datetime
    last_event: datetime


@dataclass(frozen=True)
class ResearchBenchmarkSummary:
    dataset: Path
    output: Path
    event_rows: int
    runs: int
    results: tuple[dict[str, object], ...]


def _duckdb_module():
    try:
        import duckdb
    except ImportError as error:
        raise ResearchDependencyError(
            "DuckDB is optional. Install it with: uv sync --group research"
        ) from error
    return duckdb


def _event_glob(dataset: Path) -> str:
    resolved = dataset.resolve()
    manifest = resolved / "manifest.json"
    if not manifest.is_file():
        raise ValueError(f"Research manifest was not found: {manifest}")
    files = sorted((resolved / "events").glob("part-*.parquet"))
    if not files:
        raise ValueError(f"No event Parquet files were found under {resolved / 'events'}")
    return (resolved / "events" / "part-*.parquet").as_posix()


def _connection(*, threads: int, memory_limit: str):
    if threads <= 0:
        raise ValueError("threads must be positive")
    if not MEMORY_LIMIT_PATTERN.fullmatch(memory_limit):
        raise ValueError("memory_limit must look like 512MB, 4GB, or 1TB")
    duckdb = _duckdb_module()
    connection = duckdb.connect(":memory:")
    connection.execute(f"SET threads = {threads}")
    connection.execute(f"SET memory_limit = '{memory_limit.upper()}'")
    connection.execute("SET enable_progress_bar = false")
    return connection


def inspect_research_dataset(
    dataset: Path,
    *,
    threads: int = 4,
    memory_limit: str = "4GB",
) -> ResearchInspection:
    event_glob = _event_glob(dataset)
    connection = _connection(threads=threads, memory_limit=memory_limit)
    try:
        event_rows, unique_assets, total_volume, first_event, last_event = connection.execute(
            """
            SELECT
                count(*) AS event_rows,
                count(DISTINCT asset) AS unique_assets,
                sum(amount) AS total_volume,
                min(occurred_at) AS first_event,
                max(occurred_at) AS last_event
            FROM read_parquet(?)
            """,
            [event_glob],
        ).fetchone()
        unique_accounts = connection.execute(
            """
            SELECT count(DISTINCT account_id)
            FROM (
                SELECT account_from AS account_id FROM read_parquet(?)
                UNION ALL
                SELECT account_to AS account_id FROM read_parquet(?)
            )
            """,
            [event_glob, event_glob],
        ).fetchone()[0]
    finally:
        connection.close()
    return ResearchInspection(
        dataset=dataset.resolve(),
        event_rows=event_rows,
        unique_accounts=unique_accounts,
        unique_assets=unique_assets,
        total_volume=total_volume,
        first_event=first_event.astimezone(UTC),
        last_event=last_event.astimezone(UTC),
    )


BENCHMARK_QUERIES = (
    (
        "full_scan",
        """
        SELECT count(*), sum(amount), min(occurred_at), max(occurred_at)
        FROM read_parquet(?)
        """,
    ),
    (
        "daily_asset_volume",
        """
        SELECT event_date, asset, count(*) AS events, sum(amount) AS volume
        FROM read_parquet(?)
        GROUP BY event_date, asset
        ORDER BY event_date, asset
        """,
    ),
    (
        "top_senders",
        """
        SELECT account_from, count(*) AS events, sum(amount) AS volume
        FROM read_parquet(?)
        GROUP BY account_from
        ORDER BY volume DESC
        LIMIT 100
        """,
    ),
    (
        "counterparty_edges",
        """
        SELECT account_from, account_to, asset, count(*) AS events, sum(amount) AS volume
        FROM read_parquet(?)
        GROUP BY account_from, account_to, asset
        ORDER BY events DESC
        LIMIT 100
        """,
    ),
)


def _percentile_95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def benchmark_research_dataset(
    dataset: Path,
    output: Path,
    *,
    runs: int = 3,
    threads: int = 4,
    memory_limit: str = "4GB",
) -> ResearchBenchmarkSummary:
    if runs <= 0:
        raise ValueError("runs must be positive")
    event_glob = _event_glob(dataset)
    connection = _connection(threads=threads, memory_limit=memory_limit)
    results: list[dict[str, object]] = []
    try:
        event_rows = connection.execute(
            "SELECT count(*) FROM read_parquet(?)",
            [event_glob],
        ).fetchone()[0]
        for name, query in BENCHMARK_QUERIES:
            connection.execute(query, [event_glob]).fetchall()
            durations: list[float] = []
            for _ in range(runs):
                started = perf_counter()
                connection.execute(query, [event_glob]).fetchall()
                durations.append((perf_counter() - started) * 1000)
            results.append(
                {
                    "query": name,
                    "minimum_ms": round(min(durations), 3),
                    "median_ms": round(statistics.median(durations), 3),
                    "p95_ms": round(_percentile_95(durations), 3),
                }
            )
        duckdb_version = connection.execute("SELECT version()").fetchone()[0]
    finally:
        connection.close()

    manifest = json.loads((dataset.resolve() / "manifest.json").read_text())
    payload: dict[str, Any] = {
        "dataset": dataset.resolve().as_posix(),
        "dataset_id": manifest["dataset_id"],
        "event_rows": event_rows,
        "runs": runs,
        "threads": threads,
        "memory_limit": memory_limit.upper(),
        "duckdb_version": duckdb_version,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "queries": results,
    }
    resolved_output = output.resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return ResearchBenchmarkSummary(
        dataset=dataset.resolve(),
        output=resolved_output,
        event_rows=event_rows,
        runs=runs,
        results=tuple(results),
    )
