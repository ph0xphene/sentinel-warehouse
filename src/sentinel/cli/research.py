from pathlib import Path

from sentinel.config import get_settings
from sentinel.research import (
    ResearchDependencyError,
    benchmark_research_dataset,
    generate_research_dataset,
    inspect_research_dataset,
)


def generate_dataset(
    output: Path | None,
    *,
    rows: int,
    accounts: int,
    rows_per_file: int,
    seed: int,
) -> int:
    root = get_settings().research_root
    target = output or root / "generated" / f"synthetic-seed-{seed}-rows-{rows}"
    try:
        summary = generate_research_dataset(
            target,
            event_rows=rows,
            account_rows=accounts,
            rows_per_file=rows_per_file,
            seed=seed,
        )
    except (FileExistsError, ValueError) as error:
        print(f"Research generation failed: {error}")
        return 1
    print(f"Dataset:      {summary.output}")
    print(f"Dataset ID:   {summary.dataset_id}")
    print(f"Event rows:   {summary.event_rows}")
    print(f"Accounts:     {summary.account_rows}")
    print(f"Event files:  {summary.event_files}")
    print(f"Manifest:     {summary.manifest}")
    return 0


def inspect_dataset(
    dataset: Path,
    *,
    threads: int,
    memory_limit: str,
) -> int:
    try:
        summary = inspect_research_dataset(
            dataset,
            threads=threads,
            memory_limit=memory_limit,
        )
    except (ResearchDependencyError, ValueError) as error:
        print(f"Research inspection failed: {error}")
        return 1
    print(f"Dataset:          {summary.dataset}")
    print(f"Events:           {summary.event_rows}")
    print(f"Unique accounts:  {summary.unique_accounts}")
    print(f"Unique assets:    {summary.unique_assets}")
    print(f"Total volume:     {summary.total_volume}")
    print(f"First event:      {summary.first_event.isoformat()}")
    print(f"Last event:       {summary.last_event.isoformat()}")
    return 0


def benchmark_dataset(
    dataset: Path,
    output: Path | None,
    *,
    runs: int,
    threads: int,
    memory_limit: str,
) -> int:
    target = output or (
        get_settings().research_root / "benchmarks" / f"{dataset.resolve().name}-duckdb.json"
    )
    try:
        summary = benchmark_research_dataset(
            dataset,
            target,
            runs=runs,
            threads=threads,
            memory_limit=memory_limit,
        )
    except (ResearchDependencyError, ValueError) as error:
        print(f"Research benchmark failed: {error}")
        return 1
    print(f"Dataset:    {summary.dataset}")
    print(f"Events:     {summary.event_rows}")
    print(f"Runs:       {summary.runs}")
    for result in summary.results:
        print(
            f"{result['query']:<22} min={result['minimum_ms']:>9} ms  "
            f"median={result['median_ms']:>9} ms  p95={result['p95_ms']:>9} ms"
        )
    print(f"Results:    {summary.output}")
    return 0
