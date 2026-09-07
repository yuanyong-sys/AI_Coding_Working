from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, url: str):
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

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session:
            yield session

    async def close(self) -> None:
        await self.engine.dispose()
