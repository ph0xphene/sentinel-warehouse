import hashlib
import json
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

GENERATOR_VERSION = "1.0.0"
ASSET_DEFINITIONS = (
    ("USD", "US Dollar", 2),
    ("EUR", "Euro", 2),
    ("BTC", "Bitcoin research unit", 8),
    ("ETH", "Ether research unit", 18),
)

EVENT_SCHEMA = pa.schema(
    [
        pa.field("sequence_number", pa.int64(), nullable=False),
        pa.field("external_id", pa.string(), nullable=False),
        pa.field("source_system", pa.string(), nullable=False),
        pa.field("event_type", pa.string(), nullable=False),
        pa.field("occurred_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("event_date", pa.date32(), nullable=False),
        pa.field("account_from", pa.string(), nullable=False),
        pa.field("account_to", pa.string(), nullable=False),
        pa.field("asset", pa.string(), nullable=False),
        pa.field("amount", pa.decimal128(38, 18), nullable=False),
    ]
)

ACCOUNT_SCHEMA = pa.schema(
    [
        pa.field("account_id", pa.string(), nullable=False),
        pa.field("account_type", pa.string(), nullable=False),
        pa.field("risk_segment", pa.string(), nullable=False),
    ]
)

ASSET_SCHEMA = pa.schema(
    [
        pa.field("asset_id", pa.string(), nullable=False),
        pa.field("name", pa.string(), nullable=False),
        pa.field("decimals", pa.int16(), nullable=False),
    ]
)


@dataclass(frozen=True)
class ResearchGenerationSummary:
    output: Path
    dataset_id: str
    event_rows: int
    account_rows: int
    event_files: int
    manifest: Path


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _account_id(index: int, width: int) -> str:
    return f"ACCOUNT-{index:0{width}d}"


def _event_table(
    *,
    start: int,
    stop: int,
    account_count: int,
    seed: int,
    base_time: datetime,
) -> pa.Table:
    width = max(6, len(str(account_count - 1)))
    sequence_numbers: list[int] = []
    external_ids: list[str] = []
    occurred_at: list[datetime] = []
    event_dates = []
    accounts_from: list[str] = []
    accounts_to: list[str] = []
    assets: list[str] = []
    amounts: list[Decimal] = []

    for sequence in range(start, stop):
        source_index = (seed + sequence * 17) % account_count
        destination_offset = 1 + ((seed * 13 + sequence * 31) % (account_count - 1))
        destination_index = (source_index + destination_offset) % account_count
        timestamp = base_time + timedelta(seconds=sequence * 37)
        minor_units = 1 + ((seed * 104729 + sequence * 7919) % 10_000_000)

        sequence_numbers.append(sequence)
        external_ids.append(f"RESEARCH-{seed:08d}-{sequence:016d}")
        occurred_at.append(timestamp)
        event_dates.append(timestamp.date())
        accounts_from.append(_account_id(source_index, width))
        accounts_to.append(_account_id(destination_index, width))
        assets.append(ASSET_DEFINITIONS[(seed + sequence * 7) % len(ASSET_DEFINITIONS)][0])
        amounts.append(Decimal(minor_units).scaleb(-2))

    row_count = stop - start
    return pa.Table.from_arrays(
        [
            pa.array(sequence_numbers, type=pa.int64()),
            pa.array(external_ids, type=pa.string()),
            pa.array(["research_synthetic"] * row_count, type=pa.string()),
            pa.array(["TRANSFER"] * row_count, type=pa.string()),
            pa.array(occurred_at, type=pa.timestamp("us", tz="UTC")),
            pa.array(event_dates, type=pa.date32()),
            pa.array(accounts_from, type=pa.string()),
            pa.array(accounts_to, type=pa.string()),
            pa.array(assets, type=pa.string()),
            pa.array(amounts, type=pa.decimal128(38, 18)),
        ],
        schema=EVENT_SCHEMA,
    )


def _write_reference_tables(root: Path, account_count: int) -> tuple[Path, Path]:
    width = max(6, len(str(account_count - 1)))
    accounts = pa.Table.from_arrays(
        [
            pa.array(
                [_account_id(index, width) for index in range(account_count)],
                type=pa.string(),
            ),
            pa.array(
                ["research_account"] * account_count,
                type=pa.string(),
            ),
            pa.array(
                [("low", "medium", "high")[index % 3] for index in range(account_count)],
                type=pa.string(),
            ),
        ],
        schema=ACCOUNT_SCHEMA,
    )
    assets = pa.Table.from_arrays(
        [
            pa.array([asset[0] for asset in ASSET_DEFINITIONS], type=pa.string()),
            pa.array([asset[1] for asset in ASSET_DEFINITIONS], type=pa.string()),
            pa.array([asset[2] for asset in ASSET_DEFINITIONS], type=pa.int16()),
        ],
        schema=ASSET_SCHEMA,
    )
    account_path = root / "accounts.parquet"
    asset_path = root / "assets.parquet"
    pq.write_table(accounts, account_path, compression="zstd", use_dictionary=True)
    pq.write_table(assets, asset_path, compression="zstd", use_dictionary=True)
    return account_path, asset_path


def _manifest_entry(path: Path, root: Path, rows: int) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "rows": rows,
        "bytes": path.stat().st_size,
        "sha256": _file_digest(path),
    }


def generate_research_dataset(
    output: Path,
    *,
    event_rows: int = 10_000,
    account_rows: int = 1_000,
    rows_per_file: int = 100_000,
    seed: int = 7,
) -> ResearchGenerationSummary:
    """Generate a deterministic Parquet dataset without loading it into PostgreSQL."""
    if event_rows <= 0:
        raise ValueError("event_rows must be positive")
    if account_rows < 2:
        raise ValueError("account_rows must be at least 2")
    if rows_per_file <= 0:
        raise ValueError("rows_per_file must be positive")

    resolved = output.resolve()
    if resolved.exists() and (not resolved.is_dir() or any(resolved.iterdir())):
        raise FileExistsError(f"Research dataset directory is not empty: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    parameters = {
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "event_rows": event_rows,
        "account_rows": account_rows,
        "rows_per_file": rows_per_file,
        "base_time": "2026-01-01T00:00:00Z",
    }
    dataset_id = hashlib.sha256(
        json.dumps(parameters, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    base_time = datetime(2026, 1, 1, tzinfo=UTC)

    with tempfile.TemporaryDirectory(
        prefix=f".{resolved.name}-",
        dir=resolved.parent,
    ) as temporary:
        temporary_root = Path(temporary)
        events_root = temporary_root / "events"
        events_root.mkdir()
        files: list[dict[str, object]] = []
        account_path, asset_path = _write_reference_tables(
            temporary_root,
            account_rows,
        )
        files.append(_manifest_entry(account_path, temporary_root, account_rows))
        files.append(
            _manifest_entry(
                asset_path,
                temporary_root,
                len(ASSET_DEFINITIONS),
            )
        )

        event_file_count = 0
        for start in range(0, event_rows, rows_per_file):
            stop = min(start + rows_per_file, event_rows)
            table = _event_table(
                start=start,
                stop=stop,
                account_count=account_rows,
                seed=seed,
                base_time=base_time,
            )
            event_path = events_root / f"part-{event_file_count:05d}.parquet"
            pq.write_table(
                table,
                event_path,
                compression="zstd",
                use_dictionary=True,
                write_statistics=True,
                row_group_size=min(rows_per_file, 100_000),
            )
            files.append(
                _manifest_entry(
                    event_path,
                    temporary_root,
                    stop - start,
                )
            )
            event_file_count += 1

        manifest_values = {
            "dataset_id": dataset_id,
            **parameters,
            "asset_rows": len(ASSET_DEFINITIONS),
            "event_files": event_file_count,
            "files": files,
        }
        manifest_path = temporary_root / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest_values, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if resolved.exists():
            resolved.rmdir()
        temporary_root.replace(resolved)

    return ResearchGenerationSummary(
        output=resolved,
        dataset_id=dataset_id,
        event_rows=event_rows,
        account_rows=account_rows,
        event_files=event_file_count,
        manifest=resolved / "manifest.json",
    )
