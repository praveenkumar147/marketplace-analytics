"""Build SQLAlchemy engines from environment variables (never hardcoded).

Credentials come from .env (see .env.example). URL.create() is used instead of
an f-string so passwords containing '@', ':' or '/' are escaped correctly.
"""
from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine

from src.config import get_env


def database_name() -> str:
    return get_env("MYSQL_DATABASE", "marketplace_analytics")


def build_url(include_database: bool = True) -> URL:
    return URL.create(
        drivername="mysql+pymysql",
        username=get_env("MYSQL_USER", required=True),
        password=get_env("MYSQL_PASSWORD", required=True),
        host=get_env("MYSQL_HOST", "localhost"),
        port=int(get_env("MYSQL_PORT", "3306")),
        database=database_name() if include_database else None,
        query={"charset": "utf8mb4"},
    )


def get_engine(include_database: bool = True) -> Engine:
    """Engine connected to the project database (or to the server only)."""
    return create_engine(build_url(include_database), pool_pre_ping=True)


def check_connection(engine: Engine) -> str:
    """Return the server version, raising a clear error if MySQL is unreachable."""
    with engine.connect() as conn:
        return conn.execute(text("SELECT VERSION()")).scalar_one()
