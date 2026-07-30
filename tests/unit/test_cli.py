import uuid

from sentinel.cli.main import build_parser


def test_cli_parses_db_migrate() -> None:
    args = build_parser().parse_args(["db", "migrate"])

    assert args.command == "db"
    assert args.db_command == "migrate"


def test_cli_parses_db_status() -> None:
    args = build_parser().parse_args(["db", "status"])

    assert args.db_command == "status"


def test_cli_parses_fixture_ingestion() -> None:
    args = build_parser().parse_args(
        [
            "ingest",
            "fixture",
            "data/fixtures/financial.json",
            "--quality-config",
            "configs/quality/default.json",
        ]
    )

    assert args.ingest_command == "fixture"
    assert args.path.name == "financial.json"
    assert args.quality_config.name == "default.json"


def test_cli_parses_incident_commands() -> None:
    incident_id = uuid.uuid4()

    list_args = build_parser().parse_args(["incident", "list"])
    show_args = build_parser().parse_args(["incident", "show", str(incident_id)])

    assert list_args.incident_command == "list"
    assert show_args.incident_command == "show"
    assert show_args.incident_id == incident_id


def test_cli_parses_ethereum_ingestion() -> None:
    args = build_parser().parse_args(
        ["ingest", "ethereum", "data/fixtures/ethereum_valid_transfers.json"]
    )

    assert args.ingest_command == "ethereum"
    assert args.path.name == "ethereum_valid_transfers.json"


def test_cli_parses_case_commands() -> None:
    case_id = uuid.uuid4()

    list_args = build_parser().parse_args(["case", "list"])
    show_args = build_parser().parse_args(["case", "show", str(case_id)])
    replay_args = build_parser().parse_args(["case", "replay", str(case_id)])
    import_args = build_parser().parse_args(
        ["case", "import", "data/incidents/euler_style_accounting_failure.json"]
    )
    feature_args = build_parser().parse_args(["case", "features", str(case_id)])

    assert list_args.case_command == "list"
    assert show_args.case_id == case_id
    assert replay_args.case_id == case_id
    assert import_args.path.name == "euler_style_accounting_failure.json"
    assert feature_args.case_id == case_id


def test_cli_parses_dataset_export() -> None:
    default_args = build_parser().parse_args(["dataset", "export"])
    custom_args = build_parser().parse_args(
        ["dataset", "export", "--output", "data/custom.parquet"]
    )

    assert default_args.output.name == "security_incidents.parquet"
    assert custom_args.output.name == "custom.parquet"

    validate_args = build_parser().parse_args(["dataset", "validate"])
    assert validate_args.dataset_command == "validate"
