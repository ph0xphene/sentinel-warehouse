import json
from datetime import UTC
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from sentinel.research import (
    benchmark_research_dataset,
    generate_research_dataset,
    inspect_research_dataset,
)


def _dataset_bytes(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_research_generator_is_partitioned_exact_and_deterministic(tmp_path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"

    first = generate_research_dataset(
        first_root,
        event_rows=250,
        account_rows=20,
        rows_per_file=100,
        seed=42,
    )
    second = generate_research_dataset(
        second_root,
        event_rows=250,
        account_rows=20,
        rows_per_file=100,
        seed=42,
    )

    manifest = json.loads(first.manifest.read_text())
    event_table = pq.read_table(first_root / "events")

    assert first.dataset_id == second.dataset_id
    assert first.event_files == 3
    assert manifest["event_rows"] == 250
    assert manifest["account_rows"] == 20
    assert _dataset_bytes(first_root) == _dataset_bytes(second_root)
    assert event_table.num_rows == 250
    assert event_table.schema.field("amount").type == pa.decimal128(38, 18)
    assert all(
        source != destination
        for source, destination in zip(
            event_table["account_from"].to_pylist(),
            event_table["account_to"].to_pylist(),
            strict=True,
        )
    )


def test_research_generator_refuses_to_overwrite_dataset(tmp_path) -> None:
    output = tmp_path / "dataset"
    generate_research_dataset(output, event_rows=10, account_rows=3)

    with pytest.raises(FileExistsError, match="not empty"):
        generate_research_dataset(output, event_rows=10, account_rows=3)


def test_duckdb_inspection_and_benchmark_use_parquet_directly(tmp_path) -> None:
    pytest.importorskip("duckdb")
    dataset = tmp_path / "dataset"
    generate_research_dataset(
        dataset,
        event_rows=200,
        account_rows=10,
        rows_per_file=75,
        seed=9,
    )

    inspection = inspect_research_dataset(
        dataset,
        threads=1,
        memory_limit="512MB",
    )
    benchmark_output = tmp_path / "benchmark.json"
    benchmark = benchmark_research_dataset(
        dataset,
        benchmark_output,
        runs=1,
        threads=1,
        memory_limit="512MB",
    )

    assert inspection.event_rows == 200
    assert inspection.unique_accounts == 10
    assert inspection.unique_assets == 4
    assert isinstance(inspection.total_volume, Decimal)
    assert inspection.first_event.tzinfo is UTC
    assert inspection.last_event.tzinfo is UTC
    assert benchmark.event_rows == 200
    assert {result["query"] for result in benchmark.results} == {
        "full_scan",
        "daily_asset_volume",
        "top_senders",
        "counterparty_edges",
    }
    assert json.loads(benchmark_output.read_text())["event_rows"] == 200
