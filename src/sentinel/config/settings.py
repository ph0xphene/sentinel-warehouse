from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SENTINEL_",
        extra="ignore",
    )

    database_url: str = Field(
        default="postgresql+psycopg://sentinel:sentinel@localhost:5432/sentinel"
    )
    log_level: str = "INFO"
    ethereum_chain_id: int = 1
    ethereum_chain_name: str = "mainnet"
    ethereum_rpc_url: str = ""
    ethereum_rpc_timeout_seconds: float = 10.0
    ethereum_rpc_max_retries: int = 3
    ethereum_confirmation_depth: int = 12
    ethereum_max_block_range: int = 1000
    ethereum_reorg_lookback: int = 64


@lru_cache
def get_settings() -> Settings:
    return Settings()
