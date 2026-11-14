"""Entorno de Alembic. La URL sale de CDF_DATABASE_URL o, si no está, de PGHOST/PGUSER/PGPASSWORD/PGDATABASE."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from ingesta.db import url_sqlalchemy

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def run_migrations_offline() -> None:
    context.configure(url=url_sqlalchemy(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(url_sqlalchemy(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
