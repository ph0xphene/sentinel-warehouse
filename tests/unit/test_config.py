from sentinel.config import Settings


def test_settings_have_local_database_default() -> None:
    settings = Settings(_env_file=None)

    assert settings.database_url.startswith("postgresql+psycopg://")
    assert settings.log_level == "INFO"
