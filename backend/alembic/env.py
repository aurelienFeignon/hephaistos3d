"""Migrations use the service data directory unless an explicit URL is supplied."""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from hephaistos.config import load_settings
from hephaistos.persistence.database import configure_sqlite, create_database_engine, database_url
from hephaistos.persistence.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def has_explicit_url() -> bool:
    return config.get_main_option("sqlalchemy.url") not in (None, "", "driver://user:pass@localhost/dbname")


def run_migrations_offline() -> None:
    url = (
        config.get_main_option("sqlalchemy.url")
        if has_explicit_url()
        else database_url(load_settings().hephaistos_data_dir)
    )
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"}
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    if has_explicit_url():
        connectable = engine_from_config(
            config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool
        )
        configure_sqlite(connectable)
    else:
        connectable = create_database_engine(load_settings().hephaistos_data_dir)
    try:
        with connectable.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
