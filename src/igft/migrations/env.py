"""Alembic environment. Migrations always run on a connection handed in by `igft.db.migrate`."""

from alembic import context

from igft.db.tables import metadata

config = context.config
connection = config.attributes.get("connection")

if connection is None:
    raise RuntimeError("Run migrations through igft.db.migrate (or `igft db upgrade`), not the alembic CLI.")

context.configure(connection=connection, target_metadata=metadata)

with context.begin_transaction():
    context.run_migrations()
