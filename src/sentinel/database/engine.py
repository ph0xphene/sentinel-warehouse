from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from sentinel.config import get_settings


def create_database_engine(database_url: str | None = None) -> Engine:
    """Create a SQLAlchemy engine without opening a connection immediately."""
    url = database_url or get_settings().database_url
    return create_engine(url, pool_pre_ping=True)


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """Provide a transactional session that commits or rolls back atomically."""
    session_factory = sessionmaker(bind=engine or create_database_engine())
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
