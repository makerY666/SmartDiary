from alembic import context
from sqlalchemy import engine_from_config, pool

from smartdiary import models  # noqa: F401 - register metadata for future migrations
from smartdiary.config import settings
from smartdiary.db import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
if context.is_offline_mode():
    context.configure(url=settings.database_url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = engine_from_config(
        config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
