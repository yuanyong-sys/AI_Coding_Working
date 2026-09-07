from collections.abc import AsyncIterator

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, url: str):
        parsed = make_url(url)
        if parsed.drivername.startswith("sqlite") and parsed.database and parsed.database != ":memory:":
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
        self.engine: AsyncEngine = create_async_engine(url)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_schema(self) -> None:
        # Importing registers every domain model on the shared metadata.
        from app.domains.alert import models as _alert_models  # noqa: F401
        from app.domains.audit import models as _audit_models  # noqa: F401
        from app.domains.drone import models as _drone_models  # noqa: F401
        from app.domains.ledger import models as _ledger_models  # noqa: F401
        from app.domains.mission import models as _mission_models  # noqa: F401

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            if self.engine.dialect.name == "sqlite":
                columns = {row[1] for row in (await connection.execute(text("PRAGMA table_info(mission)"))).all()}
                for name, kind in {
                    "start_time": "VARCHAR", "end_time": "VARCHAR", "drone_id": "VARCHAR",
                    "route": "VARCHAR", "ai_items": "TEXT",
                    "mission_date": "VARCHAR", "mission_type": "VARCHAR", "priority": "VARCHAR",
                    "owner": "VARCHAR", "area": "VARCHAR", "dock": "VARCHAR", "backup_drone": "VARCHAR",
                    "control_state": "VARCHAR",
                }.items():
                    if name not in columns:
                        await connection.execute(text(f"ALTER TABLE mission ADD COLUMN {name} {kind}"))
                audit_columns = {row[1] for row in (await connection.execute(text("PRAGMA table_info(audit)"))).all()}
                for name in ("subject_id", "result", "detail"):
                    if name not in audit_columns:
                        await connection.execute(text(f"ALTER TABLE audit ADD COLUMN {name} VARCHAR"))
                alert_columns = {row[1] for row in (await connection.execute(text("PRAGMA table_info(alert)"))).all()}
                if "mission_id" not in alert_columns:
                    await connection.execute(text("ALTER TABLE alert ADD COLUMN mission_id VARCHAR"))

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session:
            yield session

    async def close(self) -> None:
        await self.engine.dispose()
