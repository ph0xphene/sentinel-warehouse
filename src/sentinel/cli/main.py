import argparse
import asyncio
import uuid
from collections.abc import Sequence
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from sentinel.cli.cases import (
    import_case,
    list_cases,
    replay_case,
    report_case,
    show_case,
    show_case_features,
)
from sentinel.cli.datasets import export_dataset, validate_dataset
from sentinel.cli.incidents import list_incidents, report_incident, show_incident
from sentinel.config import get_settings
from sentinel.database import create_database_engine
from sentinel.ingestion import (
    EthereumRPCIngestionError,
    generate_fixture,
    ingest_ethereum_fixture,
    ingest_ethereum_rpc,
    ingest_fixture,
)
from sentinel.models import IncidentOrigin
from sentinel.quality import QualityConfig


def _alembic_config() -> Config:
    project_root = Path(__file__).resolve().parents[3]
    config = Config(project_root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", get_settings().database_url)
    return config


def migrate() -> None:
    command.upgrade(_alembic_config(), "head")


def migration_status() -> int:
    config = _alembic_config()
    engine = create_database_engine()
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()

    script = ScriptDirectory.from_config(config)
    head = script.get_current_head()
    print(f"Current revision: {current or 'none'}")
    print(f"Head revision:    {head}")
    if current != head:
        print("Database migrations are pending.")
        return 1
    print("Database is up to date.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sentinel")
    commands = parser.add_subparsers(dest="command", required=True)

    database = commands.add_parser("db", help="Manage the database")
    database_commands = database.add_subparsers(dest="db_command", required=True)
    database_commands.add_parser("migrate", help="Apply all pending migrations")
    database_commands.add_parser("status", help="Show the database migration status")

    incident = commands.add_parser("incident", help="Inspect security incidents")
    incident_commands = incident.add_subparsers(dest="incident_command", required=True)
    incident_list = incident_commands.add_parser("list", help="List detected incidents")
    incident_list.add_argument(
        "--origin",
        type=lambda value: IncidentOrigin(value.upper()),
        choices=tuple(IncidentOrigin),
    )
    incident_show = incident_commands.add_parser("show", help="Show incident evidence")
    incident_show.add_argument("incident_id", type=uuid.UUID)
    incident_report = incident_commands.add_parser(
        "report",
        help="Generate a static offline incident report",
    )
    incident_report.add_argument("incident_id", type=uuid.UUID)
    incident_report.add_argument("--output", type=Path)

    case = commands.add_parser("case", help="Research reproducible security incident cases")
    case_commands = case.add_subparsers(dest="case_command", required=True)
    case_commands.add_parser("list", help="List imported incident cases")
    case_show = case_commands.add_parser("show", help="Show a case and ordered attack flow")
    case_show.add_argument("case_id", type=uuid.UUID)
    case_replay = case_commands.add_parser(
        "replay", help="Replay a case and compare expected outcomes"
    )
    case_replay.add_argument("case_id", type=uuid.UUID)
    case_import = case_commands.add_parser("import", help="Import a JSON incident case fixture")
    case_import.add_argument("path", type=Path)
    case_features = case_commands.add_parser(
        "features", help="Replay a case and extract deterministic features"
    )
    case_features.add_argument("case_id", type=uuid.UUID)
    case_report = case_commands.add_parser(
        "report",
        help="Replay a case and generate a static offline investigation report",
    )
    case_report.add_argument("case_selector")
    case_report.add_argument("--output", type=Path)

    dataset = commands.add_parser("dataset", help="Build security research datasets")
    dataset_commands = dataset.add_subparsers(dest="dataset_command", required=True)
    dataset_export = dataset_commands.add_parser(
        "export", help="Export labeled incident features as Parquet"
    )
    dataset_export.add_argument(
        "--output",
        type=Path,
        default=Path("data/exports/security_incidents.parquet"),
    )
    dataset_commands.add_parser(
        "validate",
        help="Validate labels, provenance, replay outcomes, and feature determinism",
    )

    ingest = commands.add_parser("ingest", help="Run an ingestion pipeline")
    ingest_commands = ingest.add_subparsers(dest="ingest_command", required=True)
    fixture = ingest_commands.add_parser("fixture", help="Ingest a financial JSON fixture")
    fixture.add_argument("path", type=Path)
    fixture.add_argument("--quality-config", type=Path)
    ethereum = ingest_commands.add_parser(
        "ethereum", help="Ingest Ethereum ERC-20 Transfer fixtures"
    )
    ethereum.add_argument("path", type=Path)
    ethereum.add_argument("--quality-config", type=Path)
    ethereum_rpc = ingest_commands.add_parser(
        "ethereum-rpc",
        help="Ingest a finalized historical Ethereum JSON-RPC block range",
    )
    ethereum_rpc.add_argument("--from-block", type=int)
    ethereum_rpc.add_argument("--to-block", type=int, required=True)
    ethereum_rpc.add_argument("--contract", required=True)
    ethereum_rpc.add_argument("--quality-config", type=Path)

    seed = commands.add_parser("seed", help="Generate synthetic source data")
    seed_commands = seed.add_subparsers(dest="seed_command", required=True)
    generate = seed_commands.add_parser("generate", help="Generate a reconciled JSON fixture")
    generate.add_argument("output", type=Path)
    generate.add_argument("--seed", type=int, default=7)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "db":
        if args.db_command == "migrate":
            migrate()
            return 0
        return migration_status()
    if args.command == "seed":
        output = generate_fixture(args.output, args.seed)
        print(f"Fixture written to {output}")
        return 0
    if args.command == "incident":
        if args.incident_command == "list":
            return list_incidents(origin=args.origin)
        if args.incident_command == "show":
            return show_incident(args.incident_id)
        return report_incident(args.incident_id, args.output)
    if args.command == "case":
        if args.case_command == "list":
            return list_cases()
        if args.case_command == "show":
            return show_case(args.case_id)
        if args.case_command == "replay":
            return replay_case(args.case_id)
        if args.case_command == "features":
            return show_case_features(args.case_id)
        if args.case_command == "report":
            return report_case(args.case_selector, args.output)
        return import_case(args.path)
    if args.command == "dataset":
        if args.dataset_command == "validate":
            return validate_dataset()
        return export_dataset(args.output)

    quality_config = QualityConfig.from_file(args.quality_config) if args.quality_config else None
    if args.ingest_command == "ethereum-rpc":
        try:
            rpc_summary = asyncio.run(
                ingest_ethereum_rpc(
                    from_block=args.from_block,
                    to_block=args.to_block,
                    contract_address=args.contract,
                    quality_config=quality_config,
                )
            )
        except EthereumRPCIngestionError as error:
            print(f"Ethereum RPC ingestion failed: {error}")
            return 1
        summary = rpc_summary.pipeline
        print(f"Chain:        {rpc_summary.chain_name} ({rpc_summary.chain_id})")
        requested_from = (
            str(rpc_summary.requested_from_block)
            if rpc_summary.requested_from_block is not None
            else "checkpoint resume"
        )
        print(f"Requested:    {requested_from}..{rpc_summary.requested_to_block}")
        print(f"Finalized:    <= {rpc_summary.finalized_boundary}")
        print(f"Processed:    {rpc_summary.from_block}..{rpc_summary.to_block}")
        print(f"Truncated:    {rpc_summary.range_truncated}")
        print(f"Chunks:       {rpc_summary.processed_chunks}")
        print(f"Logs:         {rpc_summary.observed_logs}")
        print(f"Events:       {rpc_summary.normalized_events}")
        print(f"Checkpoint:   {rpc_summary.checkpoint_before or '-'}")
        print(f"Checkpoint+:  {rpc_summary.checkpoint_after or '-'}")
        if rpc_summary.reorganization is not None:
            print(
                "Reorganization: "
                f"{rpc_summary.reorganization.detected_at_block} -> "
                f"{rpc_summary.reorganization.common_ancestor} "
                f"({rpc_summary.reorganization.orphaned_block_count} orphaned blocks)"
            )
    elif args.ingest_command == "ethereum":
        summary = ingest_ethereum_fixture(args.path, quality_config=quality_config)
    else:
        summary = ingest_fixture(args.path, quality_config=quality_config)
    print(f"Batch:        {summary.batch_id}")
    print(f"Status:       {summary.status.value}")
    print(f"Analysis:     {summary.analysis_status.value}")
    print(f"Raw records:  {summary.raw_records}")
    print(f"Core records: {summary.core_records}")
    print(f"Attempt:      {summary.attempt_number}")
    print(f"Idempotent:   {summary.idempotent}")
    for outcome in summary.quality_results:
        status = "PASS" if outcome.passed else "FAIL"
        print(f"Quality:      {outcome.check_name}: {status}")
    for outcome in summary.invariant_results:
        status = outcome.execution_result.upper()
        print(f"Invariant:    {outcome.name}: {status}")
    return 0 if summary.status.value == "succeeded" else 1
