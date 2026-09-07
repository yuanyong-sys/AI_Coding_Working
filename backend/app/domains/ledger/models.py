from sqlalchemy import Boolean, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Ledger(Base):
    __tablename__ = "ledger"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(String)
    mileage_km: Mapped[float] = mapped_column(Float)
    clue_count: Mapped[int] = mapped_column(Integer)
    demo: Mapped[bool] = mapped_column(Boolean, default=True)
